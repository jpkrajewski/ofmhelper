"""Read a video's length, frame rate and frame times with ffprobe."""

from fractions import Fraction
from pathlib import Path

from ofmhelpers.media.ffmpeg import binary, run


def duration_s(src: Path | str, timeout_s: int) -> float:
    """Container duration in seconds of a file or an http(s) URL (ffprobe
    reads only the header). Raises if ffprobe is missing, fails, or reports
    no duration -- a caller sizing a generation by it must not guess."""
    out = run(
        [
            binary("ffprobe"),
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(src),
        ],
        timeout_s,
    )
    return float(out.strip())


def frame_rate(src: Path | str, timeout_s: int) -> float | None:
    """The first video stream's average frames per second ("24117/1000"),
    None when ffprobe reports none ("0/0")."""
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
            str(src),
        ],
        timeout_s,
    )
    text = out.strip().splitlines()[0] if out.strip() else ""
    try:
        rate = Fraction(text)
    except ValueError, ZeroDivisionError:
        return None
    return float(rate) if rate > 0 else None


def frame_times(src: Path | str, timeout_s: int) -> list[float]:
    """Every frame's start time (s) of the first video stream, in order:
    read from the packets, nothing decoded (0.12 s for 1504 frames)."""
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
            str(src),
        ],
        timeout_s,
    )
    return sorted(float(t) for t in out.split() if t and t != "N/A")
