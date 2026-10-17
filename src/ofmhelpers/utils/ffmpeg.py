"""Running ffmpeg/ffprobe: the one place that finds the binaries and turns a
failure into an error a job can show."""

import shutil
import subprocess
from pathlib import Path


def binary(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        msg = f"{name} not available"
        raise FileNotFoundError(msg)
    return path


def run(args: list[str], timeout_s: int) -> str:
    """Run ffmpeg/ffprobe and return stdout; a failure raises with the tool's
    own last lines -- that is what the job's error shows, not the whole argv."""
    try:
        return subprocess.run(
            args, check=True, capture_output=True, text=True, timeout=timeout_s
        ).stdout
    except subprocess.CalledProcessError as exc:
        tail = " / ".join((exc.stderr or "").strip().splitlines()[-3:])
        msg = f"{Path(args[0]).name} failed: {tail or exc.returncode}"
        raise RuntimeError(msg) from exc
