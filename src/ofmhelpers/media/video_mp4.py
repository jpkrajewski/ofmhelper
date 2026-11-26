# Copyright (C) 2026 jpkrajewski

import hashlib
import os
import tempfile
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from ofmhelpers.media.ffmpeg import binary, run
from ofmhelpers.media.hdr import sdr_filters

CONVERTIBLE_SUFFIXES = frozenset({".mov", ".m4v", ".webm", ".mkv"})
MP4_SUFFIX = ".mp4"

_COPIED_VIDEO = frozenset({"h264"})
_COPIED_AUDIO = frozenset({"aac", "mp3"})
_CRF = 17
_AUDIO_BITRATE = "192k"


class _Stream(BaseModel):
    model_config = ConfigDict(extra="ignore")

    codec_type: str = ""
    codec_name: str = ""
    color_transfer: str | None = None
    color_primaries: str | None = None
    color_space: str | None = None


class _Probe(BaseModel):
    model_config = ConfigDict(extra="ignore")

    streams: list[_Stream] = []

    def first(self, kind: str) -> _Stream | None:
        return next((s for s in self.streams if s.codec_type == kind), None)


def _probe(src: Path, timeout_s: int) -> _Probe:
    out = run(
        [
            binary("ffprobe"),
            "-v",
            "error",
            "-show_entries",
            ("stream=codec_type,codec_name,color_transfer,color_primaries,color_space"),
            "-of",
            "json",
            str(src),
        ],
        timeout_s,
    )
    return _Probe.model_validate_json(out)


def _video_args(video: _Stream) -> list[str]:
    if video.codec_name in _COPIED_VIDEO:
        return ["-c:v", "copy"]
    sdr = sdr_filters(video.color_transfer, video.color_primaries, video.color_space)
    bt709 = ["-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709"]
    colour = ["-vf", sdr.rstrip(","), *bt709] if sdr else []
    return [
        *colour,
        "-c:v",
        "libx264",
        "-crf",
        str(_CRF),
        "-preset",
        "medium",
        "-pix_fmt",
        "yuv420p",
    ]


def _audio_args(audio: _Stream | None) -> list[str]:
    if audio is None:
        return []
    if audio.codec_name in _COPIED_AUDIO:
        return ["-c:a", "copy"]
    return ["-c:a", "aac", "-b:a", _AUDIO_BITRATE]


def _mp4_args(probe: _Probe, src: Path, dest: Path) -> list[str]:
    video = probe.first("video")
    if video is None:
        msg = f"{src.name} has no video stream"
        raise ValueError(msg)
    audio = probe.first("audio")
    return [
        binary("ffmpeg"),
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(src),
        "-map",
        "0:v:0",
        *(["-map", "0:a:0"] if audio else []),
        *_video_args(video),
        *_audio_args(audio),
        "-movflags",
        "+faststart",
        "-f",
        "mp4",
        str(dest),
    ]


def _dest(src: Path, dest_dir: Path) -> Path:
    stat = src.stat()
    digest = hashlib.sha256(
        f"{src.resolve()}:{stat.st_size}:{stat.st_mtime_ns}".encode(),
    ).hexdigest()[:12]
    return dest_dir / f"{src.stem}-{digest}{MP4_SUFFIX}"


def as_mp4(src: Path, dest_dir: Path, timeout_s: int) -> Path:
    if src.suffix.lower() not in CONVERTIBLE_SUFFIXES:
        return src
    dest = _dest(src, dest_dir)
    if dest.is_file():
        return dest
    dest_dir.mkdir(parents=True, exist_ok=True)
    probe = _probe(src, timeout_s)
    fd, name = tempfile.mkstemp(
        dir=dest_dir,
        prefix=f".{dest.stem}.",
        suffix=MP4_SUFFIX,
    )
    os.close(fd)
    tmp = Path(name)
    try:
        run(_mp4_args(probe, src, tmp), timeout_s)
        tmp.replace(dest)
    finally:
        tmp.unlink(missing_ok=True)
    return dest
