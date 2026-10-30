"""The ffmpeg command for one copy, from its drawn parts: the trimmed
source (+ room tone), the video graph, the audio chain, the device's encode,
container flags and tags."""

import datetime
import random
from pathlib import Path

from pydantic import BaseModel

from ofmhelpers.repurpose.devices import Device
from ofmhelpers.repurpose.probe import Source
from ofmhelpers.repurpose.transformations.audio import AudioTransformer, room_tone_input
from ofmhelpers.repurpose.transformations.video import VideoTransformer
from ofmhelpers.repurpose.values import draw, draw_int, pick
from ofmhelpers.utils.ffmpeg import binary

_SEI_STRIP = "filter_units=remove_types=6"
_ISO = "%Y-%m-%dT%H:%M:%S.000000Z"
# Room tone runs this much past the video; -shortest cuts it.
_ROOM_TONE_SLACK_S = 1.0


class EncodeDraws(BaseModel, frozen=True):
    """One copy's drawn device values."""

    x264_preset: str
    crf: int | None
    bitrate_kbps: int | None
    gop: int | None
    bframes: int | None
    fps: str | None
    audio_kbps: int
    creation_time: datetime.datetime


def draw_encode(
    device: Device,
    source: Source,
    rng: random.Random,
    recorded_at: datetime.datetime | None = None,
) -> EncodeDraws:
    """`recorded_at`: the creation time to write; None = now (or within the
    device's recent days)."""
    v, tags = device.video, device.tags
    if recorded_at is not None:
        now = recorded_at.astimezone(datetime.UTC)
    else:
        now = datetime.datetime.now(datetime.UTC)
        if tags.creation_time == "recent":
            now -= datetime.timedelta(days=rng.random() * tags.recent_days)
    bitrate = draw_int(v.bitrate_kbps, rng)
    return EncodeDraws(
        x264_preset=pick(v.preset, rng) or "fast",
        crf=None if bitrate else draw_int(v.crf, rng),
        bitrate_kbps=bitrate,
        gop=draw_int(v.gop, rng),
        bframes=draw_int(v.bframes, rng),
        fps=v.frame_rate(source, rng),
        audio_kbps=round(draw(device.audio.bitrate_kbps, rng) or 192),
        creation_time=now.replace(microsecond=0),
    )


def input_args(src: Path, video: VideoTransformer) -> list[str]:
    """The source, trimmed at the input."""
    t = video.timeline
    args = ["-ss", f"{t.start_s:.3f}"] if t.start_s > 0 else []
    return [*args, "-t", f"{t.duration_s:.3f}", "-i", str(src)]


def build(
    *,
    src: Path,
    dst: Path,
    source: Source,
    video: VideoTransformer,
    audio: AudioTransformer,
    device: Device,
    encode: EncodeDraws,
    makeup_db: float,
    work_dir: Path,
) -> list[str]:
    room_tone = audio.settings.room_tone and not source.has_audio
    caption_png = video.write_caption(work_dir)
    args = [binary("ffmpeg"), "-y", "-loglevel", "error", *input_args(src, video)]
    if room_tone:
        seconds = video.timeline.duration_s / video.timeline.speed + _ROOM_TONE_SLACK_S
        args += room_tone_input(seconds, device.audio.sample_rate)
    caption_input = None
    if caption_png:
        # After the source (and the room-tone input, if any): a single-frame
        # PNG; the overlay filter repeats it for the whole video.
        caption_input = 2 if room_tone else 1
        args += ["-i", str(caption_png)]
    if encode.fps:
        args += ["-r", encode.fps]
    graph = video.filters(work_dir, device.video.scaler, caption_input)
    # The caption overlay pulls a second input into the graph: simple -vf
    # then no longer applies, it has to be -filter_complex.
    args += ["-filter_complex" if caption_input is not None else "-vf", graph]
    if source.has_audio:
        args += ["-filter:a", audio.filters(makeup_db), *_audio_encode(device, encode)]
    elif room_tone:
        args += ["-shortest", *_audio_encode(device, encode)]
    else:
        args += ["-an"]
    args += _video_encode(device, encode)
    args += _container(device, encode, has_audio=source.has_audio or room_tone)
    return [*args, str(dst)]


def _audio_encode(device: Device, encode: EncodeDraws) -> list[str]:
    a = device.audio
    args = [
        "-c:a",
        a.codec,
        "-b:a",
        f"{encode.audio_kbps}k",
        "-ar",
        str(a.sample_rate),
        "-ac",
        str(a.channels),
    ]
    return [*args, "-flags:a", "+bitexact"] if a.bitexact else args


def _video_encode(device: Device, encode: EncodeDraws) -> list[str]:
    v = device.video
    args = ["-c:v", v.codec, "-profile:v", v.profile]
    if v.level:
        args += ["-level:v", v.level]
    args += ["-preset", encode.x264_preset]
    if encode.bitrate_kbps:
        kbps = encode.bitrate_kbps
        args += [
            "-b:v",
            f"{kbps}k",
            "-maxrate:v",
            f"{round(kbps * v.maxrate_factor)}k",
            "-bufsize:v",
            f"{round(kbps * v.bufsize_factor)}k",
        ]
    elif encode.crf is not None:
        args += ["-crf", str(encode.crf)]
    if encode.gop is not None:
        args += ["-g", str(encode.gop)]
    if encode.bframes is not None:
        args += ["-bf", str(encode.bframes)]
    if v.x264_params:
        args += ["-x264-params", v.x264_params]
    if v.tag:
        args += ["-tag:v", v.tag]
    if v.strip_sei:
        args += ["-bsf:v", _SEI_STRIP]
    args += ["-pix_fmt", v.pix_fmt]
    for flag, value in (
        ("-colorspace", v.colorspace),
        ("-color_primaries", v.color_primaries),
        ("-color_trc", v.color_trc),
        ("-color_range", v.color_range),
    ):
        if value:
            args += [flag, value]
    if v.timescale:
        args += ["-video_track_timescale", str(v.timescale)]
    return args


def _container(device: Device, encode: EncodeDraws, *, has_audio: bool) -> list[str]:
    c, tags = device.container, device.tags
    # Never carry the source's own tags over.
    args = ["-map_metadata", "-1"]
    if c.bitexact:
        args += ["-fflags", "+bitexact", "-flags:v", "+bitexact"]
    if c.brand:
        args += ["-brand", c.brand]
    when = encode.creation_time.strftime(_ISO)
    scopes = [("-metadata", tags.format), ("-metadata:s:v:0", tags.video)]
    if has_audio:
        scopes.append(("-metadata:s:a:0", tags.audio))
    for flag, values in scopes:
        for key, value in values.items():
            args += [flag, f"{key}={value.replace('{creation_time}', when)}"]
    return [*args, "-movflags", "".join(c.movflags)]
