"""ffmpeg runs `utils.ffmpeg.run` can't serve: its result in the log
(`volumedetect`) or as binary stdout (a chromaprint fingerprint)."""

import subprocess
from pathlib import Path


def _completed(args: list[str], timeout_s: int) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(args, check=True, capture_output=True, timeout=timeout_s)
    except subprocess.CalledProcessError as exc:
        log = (exc.stderr or b"").decode(errors="replace")
        tail = " / ".join(log.strip().splitlines()[-3:])
        msg = f"{Path(args[0]).name} failed: {tail or exc.returncode}"
        raise RuntimeError(msg) from exc


def run_log(args: list[str], timeout_s: int) -> str:
    """Run ffmpeg and return its log (stderr); a failure raises with the
    tool's own last lines, as `utils.ffmpeg.run` does."""
    return _completed(args, timeout_s).stderr.decode(errors="replace")


def run_bytes(args: list[str], timeout_s: int) -> bytes:
    """Run ffmpeg and return its stdout as bytes."""
    return _completed(args, timeout_s).stdout
