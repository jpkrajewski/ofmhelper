"""What one ffprobe call says about the source."""

import json
from pathlib import Path

from pydantic import BaseModel

from ofmhelpers.utils.ffmpeg import binary, run


def _rate(fraction: str) -> float | None:
    """ffprobe's "30000/1001" as a number; None for "0/0"."""
    num, _, den = fraction.partition("/")
    try:
        value = float(num) / float(den or 1)
    except (ValueError, ZeroDivisionError):
        return None
    return value if value > 0 else None


class Source(BaseModel, frozen=True):
    duration: float
    width: int
    height: int
    has_audio: bool
    # Degrees a player turns the stored picture (a phone's display matrix);
    # ffmpeg turns it upright before any filter.
    rotation: int = 0
    # Average frames per second; None when ffprobe reports none (0/0).
    fps: float | None = None

    @classmethod
    def from_ffprobe(cls, out: str) -> "Source":
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
    def display_size(self) -> tuple[int, int]:
        """Width and height as played, i.e. as the filters see the frames."""
        if self.rotation % 180:
            return self.height, self.width
        return self.width, self.height

    @property
    def aspect(self) -> float:
        """Long side over short side."""
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
