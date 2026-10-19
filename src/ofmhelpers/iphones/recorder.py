"""Re-encode a video into what an iPhone camera recording of `profile` is.

ffmpeg does the encode, everything taken from the profile: codec, level,
size (portrait; landscape swaps), HLG/BT.2020 10-bit (SDR sources converted
with zscale), frame rate, timescale, bitrates, B-frames, AAC; the moov is
written after the mdat, as an iPhone does. Then the moov is rewritten with
the atoms ffmpeg cannot write (`atoms`): movie and video-track `meta`,
`tapt`, Apple media/data handlers and `alis` data references, `und`
languages, the profile's ftyp minor version, and every mvhd/tkhd/mdhd time
set to the recording's end. The moov comes after the mdat, so resizing it
moves no chunk offset.

What ffmpeg cannot make is left out: the Dolby Vision RPU, the APAC
spatial-audio track and the per-frame `mebx` metadata tracks.
"""

import math
import mmap
import random
from pathlib import Path

from ofmhelpers.iphones import atoms, sample_entries, sample_tables
from ofmhelpers.iphones.models import DeviceProfile, Recording, TimingPlan
from ofmhelpers.utils.capcut import SourceInfo, probe
from ofmhelpers.utils.ffmpeg import binary, run
from ofmhelpers.utils.mov_atoms import (
    Atom,
    duration,
    read_moov,
    set_duration,
    set_times,
    top_level,
)

# VBV around the profile's average bitrate.
_MAXRATE_FACTOR = 1.5
_BUFSIZE_FACTOR = 2
# Samples in one AAC packet.
_AAC_FRAME = 1024
# HEVC general_level_idc is 30 x the level number.
_LEVEL_IDC_PER_LEVEL = 30
# ftyp: size(4) type(4) major_brand(4), then minor_version(4).
_FTYP_MINOR_OFFSET = 12
# mdhd version 0: version/flags(4) times(8) timescale(4) duration(4), language.
_MDHD_LANG_OFFSET = 20


def frame_size(profile: DeviceProfile, src: SourceInfo) -> tuple[int, int]:
    """The profile's portrait size, or its landscape twin for a wide source."""
    w, h = profile.video.width, profile.video.height
    return (w, h) if src.height >= src.width else (h, w)


def _timing_filters(profile: DeviceProfile, plan: TimingPlan) -> str:
    """select drops the planned frames (the kept ones keep their capture
    time, so audio stays in sync); setpts on a timescale-tick base runs
    every frame from each nudge one tick later. Quoted, so the commas inside
    the expressions are not filter separators."""
    dropped = "+".join(f"eq(n,{i})" for i in plan.dropped) or "0"
    nudged = "+".join(f"gte(N,{i})" for i in plan.nudges) or "0"
    return (
        f"select='not({dropped})',"
        f"settb=1/{profile.video.timescale},"
        f"setpts='PTS+({nudged})',"
    )


def _video_filter(
    profile: DeviceProfile, src: SourceInfo, plan: TimingPlan, *, mirror: bool
) -> str:
    v = profile.video
    w, h = frame_size(profile, src)
    return (
        ("hflip," if mirror else "")
        + f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
        f"fps={v.fps},"
        + _timing_filters(profile, plan)
        + "zscale=tin=bt709:pin=bt709:min=bt709:"
        f"t={v.color_transfer}:p={v.color_primaries}:m={v.color_space}:r={v.color_range}:"
        f"npl={v.sdr_white_nits},"
        f"format={v.pix_fmt}"
    )


def _audio_filter(profile: DeviceProfile, src: SourceInfo) -> str:
    """Resampled, then padded/trimmed to whole AAC packets: an iPhone's audio
    track is one stts run of full 1024-sample packets."""
    rate = profile.audio.sample_rate
    total = math.ceil(src.duration * rate / _AAC_FRAME) * _AAC_FRAME
    return f"aresample={rate},apad=whole_len={total},atrim=end_sample={total}"


def encode_args(
    profile: DeviceProfile, src: SourceInfo, plan: TimingPlan, *, mirror: bool = False
) -> list[str]:
    v, a = profile.video, profile.audio
    level = v.level / _LEVEL_IDC_PER_LEVEL
    # info=0: no x265 version/settings SEI in the bitstream. Closed GOP:
    # keyframes are IDR, not the iPhone's CRA (`sync_nal_unit_type`) --
    # x265 4.1 crashes on cra-nal=1 with B-frames.
    x265 = (
        f"info=0:fps={v.fps}:level-idc={level:g}:bframes={v.b_frames}:"
        f"keyint={v.gop_size}:open-gop=0:high-tier=0:"
        f"colorprim={v.color_primaries}:transfer={v.color_transfer}:"
        f"colormatrix={v.color_space}:range=limited:chromaloc=0"
    )
    return [
        "-vf",
        _video_filter(profile, src, plan, mirror=mirror),
        # Keep the planned timestamps: no frame duplicated into the gaps.
        "-fps_mode",
        "passthrough",
        "-c:v",
        "libx265",
        "-profile:v",
        v.profile,
        "-b:v",
        f"{v.bitrate_kbps}k",
        "-maxrate:v",
        f"{int(v.bitrate_kbps * _MAXRATE_FACTOR)}k",
        "-bufsize:v",
        f"{v.bitrate_kbps * _BUFSIZE_FACTOR}k",
        "-x265-params",
        x265,
        # mov takes the last sample's duration from its packet; libx265's
        # packets carry none (it would be 1 tick). pts/dts passed through:
        # left out, setts drops the composition offsets.
        "-bsf:v",
        f"setts=pts=PTS:dts=DTS:duration={plan.last_frame_ticks}",
        "-tag:v",
        v.codec_tag,
        "-colorspace",
        v.color_space,
        "-color_primaries",
        v.color_primaries,
        "-color_trc",
        v.color_transfer,
        "-color_range",
        v.color_range,
        "-video_track_timescale",
        str(v.timescale),
        *(["-af", _audio_filter(profile, src)] if src.has_audio else []),
        "-c:a",
        a.codec,
        "-b:a",
        f"{a.bitrate_kbps}k",
        "-ar",
        str(a.sample_rate),
        "-ac",
        str(a.channels),
        "-movie_timescale",
        str(profile.container.movie_timescale),
        "-chunk_duration",
        str(profile.container.chunk_duration_ms * 1000),
        # Signed composition offsets (ctts v1) when the profile has them.
        *(["-movflags", "+negative_cts_offsets"] if v.negative_cts_offsets else []),
        "-map_metadata",
        "-1",
        # Becomes the video sample entry's compressorname.
        "-metadata:s:v:0",
        f"encoder={v.compressor_name}",
        # bitexact: no Lavf/Lavc version stamps in the container or AAC.
        "-fflags",
        "+bitexact",
        "-flags:v",
        "+bitexact",
        "-flags:a",
        "+bitexact",
    ]


def _handler_kind(trak: Atom) -> str:
    mdia = trak.child(b"mdia")
    hdlr = mdia.child(b"hdlr") if mdia else None
    return hdlr.payload[8:12].decode("latin-1") if hdlr else ""


class _Retagger:
    """Rewrites one ffmpeg moov into the profile's."""

    def __init__(
        self, profile: DeviceProfile, recording: Recording, size: tuple[int, int]
    ) -> None:
        self.profile = profile
        self.recording = recording
        self.size = size
        self.end = int(recording.end.timestamp())
        self.handler_names = {
            "vide": profile.handlers.video,
            "soun": profile.handlers.audio,
        }

    def moov(self, moov: Atom) -> Atom:
        # Every track and the movie end together, as on an iPhone: each
        # track's edit is cut to the shortest track (a source whose audio
        # outruns its video, or the AAC packet padding, would leave them
        # apart).
        self.length = min(
            duration(tkhd)
            for trak in moov.all(b"trak")
            if (tkhd := trak.child(b"tkhd")) is not None
        )
        children: list[Atom] = []
        for atom in moov.children or []:
            if atom.type == b"udta":
                continue
            if atom.type == b"mvhd":
                set_times(atom, self.end)
                set_duration(atom, self.length)
            children.append(self.trak(atom) if atom.type == b"trak" else atom)
        movie_meta = atoms.meta(self.profile.movie_keys, self.recording)
        if movie_meta is not None:
            children.append(movie_meta)
        return Atom(type=b"moov", children=children)

    def trak(self, trak: Atom) -> Atom:
        kind = _handler_kind(trak)
        children: list[Atom] = []
        for atom in trak.children or []:
            if atom.type == b"tkhd":
                set_times(atom, self.end)
                set_duration(atom, self.length)
            if atom.type == b"edts" and (elst := atom.child(b"elst")) is not None:
                set_duration(elst, self.length)
            children.append(self.mdia(atom, kind) if atom.type == b"mdia" else atom)
            if atom.type == b"tkhd" and kind == "vide":
                children.append(atoms.track_aperture(*self.size))
        if kind == "vide":
            track_meta = atoms.meta(self.profile.video_track_keys, self.recording)
            if track_meta is not None:
                children.append(track_meta)
        return Atom(type=b"trak", children=children)

    def mdia(self, mdia: Atom, kind: str) -> Atom:
        return Atom(
            type=b"mdia",
            children=[self.mdia_child(a, kind) for a in mdia.children or []],
        )

    def mdia_child(self, atom: Atom, kind: str) -> Atom:
        if atom.type == b"mdhd":
            set_times(atom, self.end)
            p = atom.payload
            lang = atoms.pack_language(self.profile.handlers.media_language)
            return Atom(
                type=b"mdhd",
                payload=p[:_MDHD_LANG_OFFSET]
                + lang.to_bytes(2, "big")
                + p[_MDHD_LANG_OFFSET + 2 :],
            )
        if atom.type == b"hdlr" and kind in self.handler_names:
            return atoms.media_handler(self.profile, kind, self.handler_names[kind])
        if atom.type == b"minf":
            swaps = {
                b"vmhd": atoms.video_media_header(self.profile),
                b"hdlr": atoms.data_handler(self.profile),
                b"dinf": atoms.data_information(self.profile),
            }
            return Atom(
                type=b"minf",
                children=[
                    self.stbl(c, kind) if c.type == b"stbl" else swaps.get(c.type, c)
                    for c in atom.children or []
                ],
            )
        return atom

    def stbl(self, stbl: Atom, kind: str) -> Atom:
        children = [
            sample_entries.stsd(a, kind, self.profile) if a.type == b"stsd" else a
            for a in stbl.children or []
        ]
        if kind == "vide":
            tables = sample_tables.SampleTables.from_stbl(stbl)
            children += [
                sample_tables.sync_group_description(
                    self.profile.video.encoded_sync_nal_unit_type
                ),
                sample_tables.sync_sample_to_group(tables),
                sample_tables.cslg(tables),
            ]
        return Atom(type=b"stbl", children=sample_tables.ordered(children))


def record(
    src: Path,
    dst: Path,
    profile: DeviceProfile,
    recording: Recording,
    timeout_s: int,
) -> None:
    """Write `src` to `dst` (.mov) as `profile`'s camera would have recorded
    it. `recording.duration_s` is ignored: the source's length is used."""
    info = probe(src, timeout_s)
    recording = recording.model_copy(update={"duration_s": info.duration})
    frames = math.ceil(info.duration * profile.video.fps)
    plan = profile.video.timing.plan(random.Random(recording.seed), frames)  # noqa: S311 - not cryptography
    run(
        [
            binary("ffmpeg"),
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(src),
            "-map",
            "0:v:0",
            "-map",
            "0:a:0?",
            *encode_args(profile, info, plan, mirror=recording.mirror),
            "-f",
            "mov",
            str(dst),
        ],
        timeout_s,
    )
    moov, offset = read_moov(dst)
    if top_level(dst)[-1][0] != b"moov":
        msg = f"{dst.name}: moov is not the last atom, cannot rewrite it"
        raise RuntimeError(msg)
    new_moov = _Retagger(profile, recording, frame_size(profile, info)).moov(moov)
    with dst.open("r+b") as f:
        f.seek(offset)
        f.write(new_moov.to_bytes())
        f.truncate()
    with dst.open("r+b") as f, mmap.mmap(f.fileno(), 0) as m:
        minor = profile.container.minor_version.to_bytes(4, "big")
        m[_FTYP_MINOR_OFFSET : _FTYP_MINOR_OFFSET + 4] = minor
