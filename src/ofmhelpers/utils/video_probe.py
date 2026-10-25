"""Read a video's length with ffprobe."""

from pathlib import Path

from ofmhelpers.utils.ffmpeg import binary, run


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
