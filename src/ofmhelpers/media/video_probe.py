from fractions import Fraction
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import BaseModel

from ofmhelpers.media.ffmpeg import binary, run
from ofmhelpers.media.size import Size


def _source(src: Path | str) -> str:
    if isinstance(src, Path):
        return str(src)
    if urlsplit(src).scheme not in {"http", "https"}:
        msg = "ffprobe reads a Path or an http(s) URL"
        raise ValueError(msg)
    return src


class ColourTags(BaseModel):
    color_transfer: str | None = None
    color_primaries: str | None = None
    color_space: str | None = None


class _Streams(BaseModel):
    streams: list[ColourTags]


def colour_tags(src: Path | str, timeout_s: int) -> ColourTags:
    out = run(
        [
            binary("ffprobe"),
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=color_transfer,color_primaries,color_space",
            "-of",
            "json",
            "-i",
            _source(src),
        ],
        timeout_s,
    )
    found = _Streams.model_validate_json(out).streams
    return found[0] if found else ColourTags()


def duration_s(src: Path | str, timeout_s: int) -> float:
    out = run(
        [
            binary("ffprobe"),
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            "-i",
            _source(src),
        ],
        timeout_s,
    )
    return float(out.strip())


def frame_rate(src: Path | str, timeout_s: int) -> float | None:
    out = run(
        [
            binary("ffprobe"),
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=avg_frame_rate",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            "-i",
            _source(src),
        ],
        timeout_s,
    )
    text = out.strip().splitlines()[0] if out.strip() else ""
    try:
        rate = Fraction(text)
    except ValueError, ZeroDivisionError:  # silent: not a rate
        return None
    return float(rate) if rate > 0 else None


def video_size(src: Path | str, timeout_s: int) -> Size:
    out = run(
        [
            binary("ffprobe"),
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=p=0:s=x",
            "-i",
            _source(src),
        ],
        timeout_s,
    )
    width, height = out.strip().splitlines()[0].split("x")
    return Size(width=int(width), height=int(height))


def frame_times(src: Path | str, timeout_s: int) -> list[float]:
    out = run(
        [
            binary("ffprobe"),
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "packet=pts_time",
            "-of",
            "csv=p=0",
            "-i",
            _source(src),
        ],
        timeout_s,
    )
    return sorted(float(t) for t in out.split() if t and t != "N/A")
