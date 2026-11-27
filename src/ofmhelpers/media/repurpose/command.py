# Copyright (C) 2026 jpkrajewski

import datetime as dt
lazy import random
lazy from pathlib import Path

from pydantic import BaseModel

from ofmhelpers.media.ffmpeg import binary
from ofmhelpers.media.repurpose.transformations.audio import (
    AudioTransformer,
    room_tone_input,
)
from ofmhelpers.media.repurpose.values import draw, draw_int, pick
lazy from ofmhelpers.media.repurpose.devices import Device
lazy from ofmhelpers.media.repurpose.probe import Source
lazy from ofmhelpers.media.repurpose.transformations.video import (
    Timeline,
    VideoTransformer,
)

_SEI_STRIP = "filter_units=remove_types=6"
_ISO = "%Y-%m-%dT%H:%M:%S.000000Z"
_ROOM_TONE_SLACK_S = 1.0


class EncodeDraws(BaseModel, frozen=True):
    x264_preset: str
    crf: int | None
    bitrate_kbps: int | None
    gop: int | None
    bframes: int | None
    fps: str | None
    audio_kbps: int
    creation_time: dt.datetime


def draw_encode(
    device: Device,
    source: Source,
    rng: random.Random,
    recorded_at: dt.datetime | None = None,
) -> EncodeDraws:
    v, tags = device.video, device.tags
    if recorded_at is not None:
        now = recorded_at.astimezone(dt.UTC)
    else:
        now = dt.datetime.now(dt.UTC)
        if tags.creation_time == "recent":
            now -= dt.timedelta(days=rng.random() * tags.recent_days)
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


def input_args(src: Path, timeline: Timeline) -> list[str]:
    args = ["-ss", f"{timeline.start_s:.3f}"] if timeline.start_s > 0 else []
    return [*args, "-t", f"{timeline.duration_s:.3f}", "-i", str(src)]


def build(  # noqa: PLR0913
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
    args = [binary("ffmpeg"), "-y", "-loglevel", "error"]
    args += input_args(src, video.timeline)
    if room_tone:
        seconds = video.timeline.duration_s / video.timeline.speed + _ROOM_TONE_SLACK_S
        args += room_tone_input(seconds, device.audio.sample_rate)
    caption_input = None
    if caption_png:
        caption_input = 2 if room_tone else 1
        args += ["-i", str(caption_png)]
    if encode.fps:
        args += ["-r", encode.fps]
    graph = video.filters(work_dir, device.video.scaler, caption_input)
    args += ["-filter_complex" if caption_input is not None else "-vf", graph]
    if source.has_audio:
        args += ["-filter:a", audio.filters(makeup_db)]
        args += audio_encode_args(device, encode)
    elif room_tone:
        args += ["-shortest", *audio_encode_args(device, encode)]
    else:
        args += ["-an"]
    args += _video_encode(device, encode)
    args += container_args(device, encode, has_audio=source.has_audio or room_tone)
    return [*args, str(dst)]


def audio_encode_args(device: Device, encode: EncodeDraws) -> list[str]:
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


def _video_encode(device: Device, encode: EncodeDraws) -> list[str]:  # noqa: C901
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


def container_args(
    device: Device,
    encode: EncodeDraws,
    *,
    has_audio: bool,
) -> list[str]:
    c, tags = device.container, device.tags
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
