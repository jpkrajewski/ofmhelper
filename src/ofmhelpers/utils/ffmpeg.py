"""Running ffmpeg/ffprobe: the one place that finds the binaries and turns a
failure into an error a job can show."""

import shutil
import subprocess
from collections.abc import Generator
from contextlib import contextmanager
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
    with _failures(args, timeout_s):
        return subprocess.run(
            args, check=True, capture_output=True, text=True, timeout=timeout_s
        ).stdout


def run_bytes(args: list[str], timeout_s: int) -> bytes:
    """`run` for raw output (pixels): stdout as bytes."""
    with _failures(args, timeout_s):
        return subprocess.run(
            args, check=True, capture_output=True, timeout=timeout_s
        ).stdout


@contextmanager
def _failures(args: list[str], timeout_s: int) -> Generator[None, None, None]:
    """A failure or a timeout as a RuntimeError naming only the tool."""
    name = Path(args[0]).name
    try:
        yield
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr or ""
        if isinstance(stderr, bytes):
            stderr = stderr.decode(errors="replace")
        tail = " / ".join(stderr.strip().splitlines()[-3:])
        msg = f"{name} failed: {tail or exc.returncode}"
        raise RuntimeError(msg) from exc
    except subprocess.TimeoutExpired as exc:
        msg = f"{name} timed out after {timeout_s} s"
        raise RuntimeError(msg) from exc
