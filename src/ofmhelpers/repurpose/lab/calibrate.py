"""Before a search: every ordered pair of different clips through the
optional detectors (SSCD, TMK+PDQF), as a false-positive check of their
thresholds on this run's own clips. Written to OUT/calibration.json and
shown in the report; nothing is tuned from it automatically.
"""

import itertools
from pathlib import Path

from pydantic import BaseModel

from ofmhelpers.repurpose.checker import frames, sscd, tmk
from ofmhelpers.repurpose.checker.check import CAUGHT_SHARE

_TIMEOUT_S = 900
_COPY_FPS = 1.0
_SOURCE_FPS = 10.0


class Pair(BaseModel):
    source: str
    copy_name: str
    sscd_share: float | None = None
    sscd_median_cos: float | None = None
    tmk_level1: float | None = None
    tmk_level2: float | None = None


class Calibration(BaseModel):
    sscd_threshold: float | None
    tmk_threshold: float | None
    pairs: list[Pair]

    @property
    def sscd_flagged(self) -> int:
        return sum(1 for p in self.pairs if (p.sscd_share or 0) >= CAUGHT_SHARE)

    @property
    def tmk_flagged(self) -> int:
        return sum(
            1
            for p in self.pairs
            if min(p.tmk_level1 or 0, p.tmk_level2 or 0) >= tmk.THRESHOLD
        )


def _pair(a: Path, b: Path) -> Pair:
    pair = Pair(source=a.stem, copy_name=b.stem)
    if sscd.available():
        sampled = frames.sample(b, _COPY_FPS, _TIMEOUT_S)
        found = sscd.score(a, _SOURCE_FPS, sampled, 1 / _COPY_FPS, _TIMEOUT_S)
        pair.sscd_share, pair.sscd_median_cos = found.sequence.share, found.median
    if tmk.available():
        whole = tmk.compare(a, b, _TIMEOUT_S)
        pair.tmk_level1, pair.tmk_level2 = whole.level1, whole.level2
    return pair


def cross_pairs(clips: list[Path], out: Path) -> Calibration:
    made = Calibration(
        sscd_threshold=sscd.threshold() if sscd.available() else None,
        tmk_threshold=tmk.THRESHOLD if tmk.available() else None,
        pairs=[_pair(a, b) for a, b in itertools.permutations(clips, 2)],
    )
    (out / "calibration.json").write_text(
        made.model_dump_json(indent=2), encoding="utf-8"
    )
    return made
