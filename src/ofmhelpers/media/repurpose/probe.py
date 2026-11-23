# Copyright (C) 2026 jpkrajewski

import json
lazy from pathlib import Path

from pydantic import BaseModel

from ofmhelpers.media.ffmpeg import binary, run
from ofmhelpers.media.size import Size


def _rate(fraction: str) -> float | None:
    num, _, den = fraction.partition("/")
    try:
        value = float(num) / float(den or 1)
    except ValueError, ZeroDivisionError:  # silent: not a rate (0/0)
        return None
    return value if value > 0 else None


class Source(BaseModel, frozen=True):
    duration: float
    width: int
    height: int
    has_audio: bool
    rotation: int = 0
    fps: float | None = None

    @classmethod
    def from_ffprobe(cls, out: str) -> Source:
        data = json.loads(out)
        streams = data.get("streams", [])
        video = next((s for s in streams if s["codec_type"] == "video"), None)
        if video is None:
            msg = "the file has no video stream"
            raise ValueError(msg)
        side = video.get("side_data_list", [])
        return cls(
            duration=float(data["format"]["duration"]),
            width=video["width"],
            height=video["height"],
            has_audio=any(s["codec_type"] == "audio" for s in streams),
            rotation=next((int(d["rotation"]) for d in side if "rotation" in d), 0),
            fps=_rate(video.get("avg_frame_rate", "0/0")),
        )

    @property
    def display_size(self) -> Size:
        if self.rotation % 180:
            return Size(width=self.height, height=self.width)
        return Size(width=self.width, height=self.height)

    @property
    def aspect(self) -> float:
        return max(self.width, self.height) / min(self.width, self.height)


def probe(src: Path, timeout_s: int) -> Source:
    out = run(
        [
            binary("ffprobe"),
            "-v",
            "error",
            "-show_entries",
            (
                "stream=codec_type,width,height,avg_frame_rate"
                ":stream_side_data=rotation:format=duration"
            ),
            "-of",
            "json",
            str(src),
        ],
        timeout_s,
    )
    return Source.from_ffprobe(out)
