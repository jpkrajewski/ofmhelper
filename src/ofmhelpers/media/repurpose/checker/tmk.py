"""TMK+PDQF -- Meta's video hash (ThreatExchange `tmk/`): PDQ-style frame
features pooled over time with periodic kernels, so re-encoded, trimmed or
shifted copies still match. One score per video pair: level 1 (cosine of
the frame-feature averages) and level 2 (the time kernels), both 0..1;
ThreatExchange's default call: both >= 0.7 = the same video. Measured
2026-10-26 on a 6 s reel: plain 720p re-encode 0.998, trimmed 0.3 s +
cropped 5% + saturation 0.963.

Optional: the C++ tools built from ThreatExchange (commit
295fd16e53c553443d8cfcb83ddc2f1c1d826fbd: `cd tmk/cpp && make`) in
`OFM_TMK_DIR` (tmk-hash-video, tmk-two-level-score); `available()` says
whether it runs here. A source's hash is kept per file (the lab checks
hundreds of copies of the same few sources) in the uploads root's `.tmk`
folder, next to the other caches there (`.previews`, `.edit`); one unused
for a day is dropped when the next is made. It was the container's temp dir
until 2026-11-11: never cleaned while the container lived.
"""

import hashlib
import subprocess
import tempfile
import time
from pathlib import Path

from pydantic import BaseModel

from ofmhelpers.config import settings
from ofmhelpers.media.ffmpeg import binary

THRESHOLD = 0.7
# A stored source hash nobody compared against for this long goes.
_KEEP_S = 86400
_TOOLS = ("tmk-hash-video", "tmk-two-level-score")


class TmkScore(BaseModel, frozen=True):
    level1: float
    level2: float
    threshold: float = THRESHOLD

    @property
    def matched(self) -> bool:
        return self.level1 >= self.threshold and self.level2 >= self.threshold


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
    """Where the sources' hashes are kept."""
    return Path(settings.infra.uploads_root) / ".tmk"


def _drop_old(folder: Path) -> None:
    oldest = time.time() - _KEEP_S
    for stored in folder.glob("*.tmk"):
        if stored.stat().st_mtime < oldest:
            stored.unlink(missing_ok=True)


def _source(path: str, mtime: float, timeout_s: int) -> Path:
    """The stored hash of the source at `path`, made once per file version."""
    key = hashlib.sha1(f"{path}|{mtime}".encode(), usedforsecurity=False).hexdigest()
    folder = sources_dir()
    stored = folder / f"{key}.tmk"
    if stored.is_file():
        # Used now: `_drop_old` goes by this time.
        stored.touch()
        return stored
    folder.mkdir(parents=True, exist_ok=True)
    _drop_old(folder)
    # Under its name only once whole: a hash cut off half-written would be
    # compared against for a day.
    partial = _hash(Path(path), folder / f"{key}.part", timeout_s)
    return partial.replace(stored)


def compare(source: Path, copy: Path, timeout_s: int) -> TmkScore:
    stored = _source(str(source), source.stat().st_mtime, timeout_s)
    with tempfile.TemporaryDirectory(prefix="tmk_") as tmp:
        query = _hash(copy, Path(tmp) / "copy.tmk", timeout_s)
        # Gates off (-1): always print both scores, not only for a match.
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
        return TmkScore(level1=0.0, level2=0.0)
    return TmkScore(level1=round(float(out[0]), 4), level2=round(float(out[1]), 4))
