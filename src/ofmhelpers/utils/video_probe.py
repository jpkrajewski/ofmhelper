"""Read a video's length with ffprobe."""

import shutil
import subprocess
from pathlib import Path


def duration_s(src: Path, timeout_s: int) -> float:
    """Container duration in seconds. Raises if ffprobe is missing, fails, or
    reports no duration -- a caller sizing a generation by it must not guess."""
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        msg = "ffprobe not available"
        raise FileNotFoundError(msg)
    out = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(src),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout_s,
    ).stdout.strip()
    return float(out)
