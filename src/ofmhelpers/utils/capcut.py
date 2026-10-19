"""CapCut-style edit + export of a video, in one ffmpeg pass.

Slider values are CapCut's; CapCut's internals are closed, so each is mapped
to ffmpeg filters on a linear scale calibrated against one real CapCut
export (scale 110 / exposure -3 / contrast 5 / sharpen 15 / particles 10 on
a 720p clip: centred 111% zoom, midtones +2, saturation +5.5%, fine detail
+28%, no measurable grain) -- close, not pixel-equal:

- scale (%)        -> zoom-in crop, output keeps the source framing size
- move_down (%)    -> picture moved down by this share of the frame height
- exposure -50..50 -> `exposure` filter, +-1/3 stop at the ends
- contrast -50..50 -> `eq` gamma and saturation (CapCut's contrast lifts the
                      midtones and colour; it barely moves black/white)
- sharpen 0..100   -> `unsharp` luma amount 0..2 at 4K
- particles 0..100 -> faint temporal noise at 4K (CapCut's shows almost none)
- speed (%)        -> playback this much faster, audio pitch kept (atempo)
- trim_head/tail   -> seconds cut off the front / back

On top, every run draws a small random jitter (the uniqify "medium" ranges:
saturation/contrast/brightness/zoom/grain/volume) so no two runs match.

The pass: one ffprobe (duration, size, audio) + one ffmpeg (trim, crop,
colour, 4K upscale, sharpen, grain, speed, CapCut encode and tags), then two
same-length byte patches of the tags ffmpeg cannot write itself.
"""

import datetime
import json
import mmap
import random
from pathlib import Path

from pydantic import BaseModel, Field

from ofmhelpers.utils.ffmpeg import binary, run

Range = tuple[float, float]

# uniqify's MEDIUM ranges, 2% boost included, as used there.
_JITTER_SATURATION: Range = (0.949, 1.0816)
_JITTER_CONTRAST: Range = (0.9694, 1.051)
_JITTER_BRIGHTNESS: Range = (-0.0204, 0.0204)
_JITTER_ZOOM: Range = (1.0, 1.0022)
_JITTER_NOISE: Range = (0.0, 5.1)
_JITTER_VOLUME: Range = (0.9694, 1.0306)

# Slider calibration (see module docstring).
_EXPOSURE_STOPS_PER_UNIT = 1 / 150
_CONTRAST_GAMMA_PER_UNIT = 1 / 200
_CONTRAST_SATURATION_PER_UNIT = 1 / 90
_SHARPEN_PER_UNIT = 1 / 50
_PARTICLES_GRAIN_PER_UNIT = 0.1

# Video/audio encode of a CapCut desktop 4K export, copied from a real one:
# H.264 Main level 5.1 tagged avc1, 59.94 fps on a 1/60000 timebase, ~33 Mb/s
# actual (CapCut's "bitrate" tag says its 55 Mb/s target), no B-frames
# (CapCut uses the hardware encoder), bt709 limited range; AAC LC 44.1 kHz
# stereo ~192 kb/s.
_FPS = "60000/1001"
_TIMESCALE = "60000"
_VIDEO_BITRATE = "28M"
# Target set under the measured ~33 Mb/s: x264's average-bitrate mode lands
# above it, and overshoots ~2x on short grainy clips without the VBV cap.

_VIDEO_MAXRATE = "31M"
_VIDEO_BUFSIZE = "28M"
# veryfast: at this bitrate a slower preset buys nothing visible.
_X264_PRESET = "veryfast"
# Colour VUI goes through x264 itself: ffmpeg's -color_* flags leave
# primaries/transfer "unknown" in the H.264 stream.
_X264_PARAMS = "bframes=0:colorprim=bt709:transfer=bt709:colormatrix=bt709"
# x264 writes its version + settings as an SEI NAL (type 6); CapCut's
# hardware encoder writes none, so every SEI is dropped from the bitstream.
_DROP_SEI = "filter_units=remove_types=6"
_AUDIO_BITRATE = "192k"
_AUDIO_RATE = "44100"
_AUDIO_CHANNELS = "2"
# CapCut's 4K export: short side 2160 (2160x3840 portrait, 3840x2160 landscape).
_SHORT_SIDE_4K = 2160


def _even(v: float) -> int:
    return int(v / 2) * 2


class SourceInfo(BaseModel, frozen=True):
    """What one ffprobe call says about the source."""

    duration: float
    width: int
    height: int
    has_audio: bool

    @classmethod
    def from_ffprobe(cls, out: str) -> "SourceInfo":
        probe = json.loads(out)
        streams = probe.get("streams", [])
        video = next(s for s in streams if s["codec_type"] == "video")
        return cls(
            duration=float(probe["format"]["duration"]),
            width=video["width"],
            height=video["height"],
            has_audio=any(s["codec_type"] == "audio" for s in streams),
        )

    def size_4k(self) -> tuple[int, int]:
        """Output size: short side 2160, long side keeps the aspect."""
        if self.width > self.height:
            return _even(_SHORT_SIDE_4K * self.width / self.height), _SHORT_SIDE_4K
        return _SHORT_SIDE_4K, _even(_SHORT_SIDE_4K * self.height / self.width)


class Jitter(BaseModel, frozen=True):
    """The random per-run nudge on top of the preset."""

    saturation: float
    contrast: float
    brightness: float
    zoom: float
    noise: float
    volume: float

    @classmethod
    def sample(cls, rng: random.Random) -> "Jitter":
        return cls(
            saturation=round(rng.uniform(*_JITTER_SATURATION), 3),
            contrast=round(rng.uniform(*_JITTER_CONTRAST), 3),
            brightness=round(rng.uniform(*_JITTER_BRIGHTNESS), 3),
            zoom=round(rng.uniform(*_JITTER_ZOOM), 4),
            noise=round(rng.uniform(*_JITTER_NOISE), 1),
            volume=round(rng.uniform(*_JITTER_VOLUME), 3),
        )


class CapcutPreset(BaseModel, frozen=True):
    """Slider values as typed into CapCut, plus speed and trims."""

    scale: float = Field(100.0, ge=100.0, le=400.0)
    move_down: float = Field(0.0, ge=-50.0, le=50.0)
    exposure: float = Field(0.0, ge=-50.0, le=50.0)
    contrast: float = Field(0.0, ge=-50.0, le=50.0)
    sharpen: float = Field(0.0, ge=0.0, le=100.0)
    particles: float = Field(0.0, ge=0.0, le=100.0)
    speed: float = Field(0.0, ge=-50.0, le=100.0)
    trim_head: float = Field(0.0, ge=0.0)
    trim_tail: float = Field(0.0, ge=0.0)
    # Flipped left-right (CapCut's Mirror button).
    mirror: bool = False

    @property
    def tempo(self) -> float:
        return 1.0 + self.speed / 100.0

    def crop(self, src: SourceInfo, zoom: float) -> str:
        """The zoom-in window, its centre moved up by move_down so the
        picture moves down; clamped inside the frame."""
        z = self.scale / 100.0 * zoom
        w, h = _even(src.width / z), _even(src.height / z)
        x = _even((src.width - w) / 2)
        y = (src.height - h) / 2 - src.height * self.move_down / 100.0
        y = _even(min(max(y, 0.0), src.height - h))
        return f"crop={w}:{h}:{x}:{y}"

    def video_filter(self, src: SourceInfo, jitter: Jitter) -> str:
        """Crop, colour, exposure and the jitter's grain at source size (cheap,
        and the grain size uniqify uses), then the exact 4K upscale, then
        sharpen and particles at 4K as CapCut renders them."""
        out_w, out_h = src.size_4k()
        gamma = 1.0 + self.contrast * _CONTRAST_GAMMA_PER_UNIT
        saturation = (
            1.0 + self.contrast * _CONTRAST_SATURATION_PER_UNIT
        ) * jitter.saturation
        eq = (
            f"eq=contrast={jitter.contrast}:brightness={jitter.brightness}"
            f":saturation={saturation:.4f}:gamma={gamma:.4f}"
        )
        vf = ["hflip"] if self.mirror else []
        vf += [self.crop(src, jitter.zoom), eq]
        # Grain before exposure: `exposure` outputs float RGB, which `noise`
        # treats as 8-bit and turns into colour static.
        if jitter.noise:
            vf.append(f"noise=alls={jitter.noise:g}:allf=t")
        if self.exposure:
            stops = self.exposure * _EXPOSURE_STOPS_PER_UNIT
            vf.append(f"exposure=exposure={stops:.4f}")
        vf.append(f"scale={out_w}:{out_h}:flags=lanczos")
        if self.sharpen:
            vf.append(f"unsharp=5:5:{self.sharpen * _SHARPEN_PER_UNIT:.4f}")
        if self.particles:
            grain = self.particles * _PARTICLES_GRAIN_PER_UNIT
            vf.append(f"format=yuv420p,noise=alls={grain:g}:allf=t")
        vf.append(f"setpts=PTS/{self.tempo:g}")
        vf.append("format=yuv420p")
        return ",".join(vf)

    def audio_filter(self, jitter: Jitter) -> str:
        return f"atempo={self.tempo:g},volume={jitter.volume}"


DEFAULT_PRESET = CapcutPreset(
    scale=110,
    move_down=2,
    exposure=-3,
    contrast=5,
    sharpen=15,
    particles=10,
    speed=3,
    trim_head=0.3,
    trim_tail=0.3,
)


# Container tags of a CapCut desktop export, copied from a real one.
# major_brand/minor_version/compatible_brands come from ftyp, which ffmpeg's
# mp4 muxer already writes as isom/512/isomiso2avc1mp41 for H.264.
_CAPCUT_FORMAT_TAGS = {
    "Hw": "1",
    "bitrate": "55000000",
    "maxrate": "0",
    "te_is_reencode": "1",
}
_CAPCUT_HANDLERS = {"v": "VideoHandler", "a": "SoundHandler"}
# ffmpeg can't write `encoder` itself: without bitexact it stamps its own Lavf
# version over it, with bitexact it drops the value. So the value goes out
# under a same-length placeholder key, renamed in place after the mux --
# equal length, so no atom size or chunk offset moves.
_CAPCUT_ENCODER = "Lavf61.1.100"
_ENCODER_KEY = b"encoder"
_PLACEHOLDER_KEY = b"zzcodec"
# VisualSampleEntry (ISO 14496-12): compressorname sits 42 bytes past the
# box type, after reserved/data_reference_index/pre_defined/size/resolution/
# frame_count fields.
_COMPRESSOR_NAME_OFFSET = 42
_COMPRESSOR_NAME_LEN = 32


def probe(src: Path, timeout_s: int) -> SourceInfo:
    out = run(
        [
            binary("ffprobe"),
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type,width,height:format=duration",
            "-of",
            "json",
            str(src),
        ],
        timeout_s,
    )
    return SourceInfo.from_ffprobe(out)


def capcut_metadata_args(now: datetime.datetime) -> list[str]:
    """ffmpeg args that drop the source's tags and write CapCut's instead,
    stamped with `now` as creation_time."""
    iso = now.strftime("%Y-%m-%dT%H:%M:%S.000000Z")
    # bitexact: no ffmpeg/encoder version stamps on the file, the video stream
    # or inside the AAC data (ffmpeg's encoder writes "Lavc..." there).
    args = ["-map_metadata", "-1", "-fflags", "+bitexact"]
    args += ["-flags:v", "+bitexact", "-flags:a", "+bitexact"]
    args += ["-brand", "isom"]
    # CapCut's key order: creation_time, its own keys, encoder last.
    tags = {
        "creation_time": iso,
        **_CAPCUT_FORMAT_TAGS,
        _PLACEHOLDER_KEY.decode(): _CAPCUT_ENCODER,
    }
    for key, value in tags.items():
        args += ["-metadata", f"{key}={value}"]
    for stream, handler in _CAPCUT_HANDLERS.items():
        args += [
            f"-metadata:s:{stream}:0",
            f"handler_name={handler}",
            f"-metadata:s:{stream}:0",
            f"creation_time={iso}",
        ]
    # use_metadata_tags: without it the mp4 muxer drops the non-standard keys.
    return [*args, "-movflags", "+faststart+use_metadata_tags"]


def _encode_args(src: SourceInfo, preset: CapcutPreset, jitter: Jitter) -> list[str]:
    args = ["-vf", preset.video_filter(src, jitter)]
    if src.has_audio:
        args += ["-filter:a", preset.audio_filter(jitter)]
    return [
        *args,
        "-r",
        _FPS,
        "-c:v",
        "libx264",
        "-preset",
        _X264_PRESET,
        "-b:v",
        _VIDEO_BITRATE,
        "-maxrate:v",
        _VIDEO_MAXRATE,
        "-bufsize:v",
        _VIDEO_BUFSIZE,
        "-x264-params",
        _X264_PARAMS,
        "-profile:v",
        "main",
        "-level:v",
        "5.1",
        "-tag:v",
        "avc1",
        "-bsf:v",
        _DROP_SEI,
        "-colorspace",
        "bt709",
        "-color_primaries",
        "bt709",
        "-color_trc",
        "bt709",
        "-color_range",
        "tv",
        "-video_track_timescale",
        _TIMESCALE,
        "-c:a",
        "aac",
        "-b:a",
        _AUDIO_BITRATE,
        "-ar",
        _AUDIO_RATE,
        "-ac",
        _AUDIO_CHANNELS,
    ]


def apply_preset(
    src: Path,
    dst: Path,
    preset: CapcutPreset,
    timeout_s: int,
    seed: int | None = None,
) -> Jitter:
    """Edit `src` with `preset` plus a random jitter and write it to `dst`
    encoded and tagged like a CapCut 4K export. Returns the jitter drawn."""
    info = probe(src, timeout_s)
    keep = info.duration - preset.trim_head - preset.trim_tail
    if keep <= 0:
        msg = f"{src.name} is {info.duration:.2f}s, shorter than the trims"
        raise ValueError(msg)
    jitter = Jitter.sample(random.Random(seed))  # noqa: S311 - not cryptography
    run(
        [
            binary("ffmpeg"),
            "-y",
            "-loglevel",
            "error",
            "-ss",
            f"{preset.trim_head:g}",
            "-t",
            f"{keep:.3f}",
            "-i",
            str(src),
            *_encode_args(info, preset, jitter),
            *capcut_metadata_args(datetime.datetime.now(datetime.UTC)),
            str(dst),
        ],
        timeout_s,
    )
    with dst.open("r+b") as f, mmap.mmap(f.fileno(), 0) as m:
        _rename_encoder_key(m)
        _clear_compressor_name(m)
    return jitter


def _find(m: mmap.mmap, needle: bytes, start: int = 0) -> int:
    at = m.find(needle, start)
    if at < 0:
        msg = f"{needle!r} not found in the encoded file"
        raise RuntimeError(msg)
    return at


def _rename_encoder_key(m: mmap.mmap) -> None:
    """Rename the placeholder mdta key in the keys atom to `encoder`."""
    start = _find(m, b"mdta" + _PLACEHOLDER_KEY) + len(b"mdta")
    m[start : start + len(_ENCODER_KEY)] = _ENCODER_KEY


def _clear_compressor_name(m: mmap.mmap) -> None:
    """Zero the avc1 sample entry's 32-byte compressorname, which ffmpeg fills
    with "Lavc libx264" (ffprobe's stream `encoder`) and CapCut leaves empty."""
    # Searched from stsd on: "avc1" also appears earlier, among ftyp's brands.
    entry = _find(m, b"avc1", _find(m, b"stsd"))
    start = entry + len(b"avc1") + _COMPRESSOR_NAME_OFFSET
    m[start : start + _COMPRESSOR_NAME_LEN] = bytes(_COMPRESSOR_NAME_LEN)
