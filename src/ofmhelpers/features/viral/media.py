"""A downloaded reel's sound: whether the file has any, and putting a
separately served audio stream back in (ffmpeg, streams copied)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ofmhelpers.media.ffmpeg import binary, run

if TYPE_CHECKING:
    from pathlib import Path

_TIMEOUT_S = 120


def has_audio(path: Path) -> bool:
    out = run(
        [
            binary("ffprobe"),
            "-v",
            "error",
            "-select_streams",
            "a",
            "-show_entries",
            "stream=index",
            "-of",
            "csv=p=0",
            str(path),
        ],
        timeout_s=_TIMEOUT_S,
    )
    return bool(out.strip())


def add_audio(video: Path, audio: Path) -> None:
    """`video` with `audio`'s sound, in place."""
    out = video.with_name(f"{video.stem}.muxed.mp4")
    run(
        [
            binary("ffmpeg"),
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(video),
            "-i",
            str(audio),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c",
            "copy",
            "-shortest",
            "-movflags",
            "+faststart",
            str(out),
        ],
        timeout_s=_TIMEOUT_S,
    )
    out.replace(video)
