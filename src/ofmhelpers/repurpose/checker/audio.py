"""Audio similarity by Chromaprint (the AcoustID fingerprint; ffmpeg's own
`chromaprint` muxer, no fpcalc needed): one 32-bit item per ~0.124 s. Two
tracks are compared at the time offset where most bits agree; random audio
agrees on ~50% of bits, the same audio on ~90%+. Then, like the picture's
frames, the copy in ~2 s windows: the share of windows that find the same
audio in the source (each may drift ~2 s off the track's offset: tempo
changes).
"""

import struct
from pathlib import Path

from pydantic import BaseModel

from ofmhelpers.repurpose.runner import run_bytes
from ofmhelpers.utils.ffmpeg import binary

_BITS = 32
_ITEM_S = 4096 / 3 / 11025
# Offsets tried: +-10 s.
_MAX_OFFSET_ITEMS = 80
# Fewer overlapping items than this is no comparison.
_MIN_OVERLAP = 16
# Share of agreeing bits from which two stretches count as the same audio.
SAME_AUDIO = 0.7
# One window of the copy: ~2 s.
_WINDOW = 16
# How far a window may sit off the whole track's best offset: ~2 s.
_DRIFT_ITEMS = 16


class AudioScore(BaseModel, frozen=True):
    # The whole track at its best offset: share of agreeing bits.
    similarity: float
    offset_s: float
    threshold: float
    # Share of the copy's ~2 s windows that find the same audio.
    matched_share: float


def fingerprint(path: Path, timeout_s: int) -> list[int]:
    raw = run_bytes(
        [
            binary("ffmpeg"),
            "-v",
            "error",
            "-i",
            str(path),
            "-vn",
            "-ac",
            "1",
            "-f",
            "chromaprint",
            "-fp_format",
            "raw",
            "-",
        ],
        timeout_s,
    )
    count = len(raw) // 4
    return list(struct.unpack(f"<{count}I", raw[: count * 4]))


def _agreement(a: list[int], b: list[int], offset: int) -> float | None:
    pairs = list(zip(a[max(0, offset) :], b[max(0, -offset) :], strict=False))
    if len(pairs) < _MIN_OVERLAP:
        return None
    differing = sum((x ^ y).bit_count() for x, y in pairs)
    return 1 - differing / (_BITS * len(pairs))


def _window_matches(source: list[int], window: list[int], at: int) -> bool:
    """`window` matches somewhere within `_DRIFT_ITEMS` of source item `at`."""
    for start in range(at - _DRIFT_ITEMS, at + _DRIFT_ITEMS + 1):
        if start < 0 or start + len(window) > len(source):
            continue
        differing = sum(
            (x ^ y).bit_count() for x, y in zip(source[start:], window, strict=False)
        )
        if 1 - differing / (_BITS * len(window)) >= SAME_AUDIO:
            return True
    return False


def _matched_share(source: list[int], copy: list[int], offset: int) -> float:
    starts = range(0, len(copy) - _WINDOW + 1, _WINDOW)
    if not starts:
        return 0.0
    hits = sum(
        _window_matches(source, copy[s : s + _WINDOW], s + offset) for s in starts
    )
    return round(hits / len(starts), 3)


def compare(source: list[int], copy: list[int]) -> AudioScore:
    best, best_offset = 0.0, 0
    for offset in range(-_MAX_OFFSET_ITEMS, _MAX_OFFSET_ITEMS + 1):
        agreement = _agreement(source, copy, offset)
        if agreement is not None and agreement > best:
            best, best_offset = agreement, offset
    return AudioScore(
        similarity=round(best, 4),
        offset_s=round(best_offset * _ITEM_S, 2),
        threshold=SAME_AUDIO,
        matched_share=_matched_share(source, copy, best_offset),
    )
