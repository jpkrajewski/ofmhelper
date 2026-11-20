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
    with _failures(args, timeout_s):
        return subprocess.run(
            args, check=True, capture_output=True, text=True, timeout=timeout_s
        ).stdout


def run_bytes(args: list[str], timeout_s: int) -> bytes:
    with _failures(args, timeout_s):
        return subprocess.run(
            args, check=True, capture_output=True, timeout=timeout_s
        ).stdout


def run_log(args: list[str], timeout_s: int) -> str:
    with _failures(args, timeout_s):
        done = subprocess.run(args, check=True, capture_output=True, timeout=timeout_s)
    return done.stderr.decode(errors="replace")


@contextmanager
def _failures(args: list[str], timeout_s: int) -> Generator[None]:
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
