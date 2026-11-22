import hashlib
import subprocess
import tempfile
import time
from pathlib import Path

from ofmhelpers.config import settings
from ofmhelpers.media.ffmpeg import binary
from ofmhelpers.media.repurpose.checker.schema import TmkScore

THRESHOLD = 0.7
_KEEP_S = 86400
_TOOLS = ("tmk-hash-video", "tmk-two-level-score")


def _tool(name: str) -> Path:
    return Path(settings.repurpose.tmk_dir or "") / name


def available() -> bool:
    return bool(settings.repurpose.tmk_dir) and all(_tool(t).is_file() for t in _TOOLS)


def _hash(video: Path, dst: Path, timeout_s: int) -> Path:
    subprocess.run(
        [
            str(_tool("tmk-hash-video")),
            "-f",
            binary("ffmpeg"),
            "-i",
            str(video),
            "-o",
            str(dst),
        ],
        check=True,
        capture_output=True,
        timeout=timeout_s,
    )
    return dst


def sources_dir() -> Path:
    return Path(settings.infra.uploads_root) / ".tmk"


def _drop_old(folder: Path) -> None:
    oldest = time.time() - _KEEP_S
    for stored in folder.glob("*.tmk"):
        if stored.stat().st_mtime < oldest:
            stored.unlink(missing_ok=True)


def _source(path: str, mtime: float, timeout_s: int) -> Path:
    key = hashlib.sha1(f"{path}|{mtime}".encode(), usedforsecurity=False).hexdigest()
    folder = sources_dir()
    stored = folder / f"{key}.tmk"
    if stored.is_file():
        stored.touch()
        return stored
    folder.mkdir(parents=True, exist_ok=True)
    _drop_old(folder)
    partial = _hash(Path(path), folder / f"{key}.part", timeout_s)
    return partial.replace(stored)


def compare(source: Path, copy: Path, timeout_s: int) -> TmkScore:
    stored = _source(str(source), source.stat().st_mtime, timeout_s)
    with tempfile.TemporaryDirectory(prefix="tmk_") as tmp:
        query = _hash(copy, Path(tmp) / "copy.tmk", timeout_s)
        out = subprocess.run(
            [
                str(_tool("tmk-two-level-score")),
                "--c1",
                "-1",
                "--c2",
                "-1",
                str(stored),
                str(query),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        ).stdout.split()
    if len(out) < 2:  # noqa: PLR2004 - "level1 level2 file file"
        return TmkScore(level1=0.0, level2=0.0, threshold=THRESHOLD)
    return TmkScore(
        level1=round(float(out[0]), 4),
        level2=round(float(out[1]), 4),
        threshold=THRESHOLD,
    )
