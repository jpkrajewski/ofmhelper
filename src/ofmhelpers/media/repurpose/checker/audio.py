import struct
from pathlib import Path

from pydantic import BaseModel

from ofmhelpers.media.ffmpeg import binary, run_bytes

_BITS = 32
_ITEM_S = 4096 / 3 / 11025
_MAX_OFFSET_ITEMS = 80
_MIN_OVERLAP = 16
SAME_AUDIO = 0.7
_WINDOW = 16
_DRIFT_ITEMS = 16


class AudioScore(BaseModel, frozen=True):
    similarity: float
    offset_s: float
    threshold: float
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
