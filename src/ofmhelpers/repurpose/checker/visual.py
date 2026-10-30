"""Picture similarity, detector-style, on normalized frames
(`normalize.py`): every source frame is stored as its full and centre-crop
hashes; every copy frame is queried with both views in all 8 rotations /
flips (`normalize.dihedral`; PDQ's own `compute_dihedral`), and the closest
source frame counts. Then `sequence.best_line`: how many of the copy's
matched frames line up in time with the source.

Hashes: PDQ (Meta, 256 bits, the production-grade one; `pdqhash`, built
from source in the image), and imagehash's pHash (DCT), dHash (row
gradients), wHash (Haar wavelets), 64 bits each. A distance at or under
`SAME_PICTURE[kind]` bits = the same picture. Calibrated 2026-10-26 (source
at 10 frames/s, copy at 1/s) on 16 unrelated reels (236 ordered pairs, two
template remakes -- same spot, framing and sound, another girl -- left out)
against copies of one reel: per hash, one step under the highest threshold
at which no unrelated pair matched in order (repurpose/CLAUDE.md). PDQ's own
31 (Meta's starting point) caught only a plain re-encode: the handheld
shake / tilt alone moves it ~55 bits.
"""

import statistics
from collections.abc import Callable
from enum import StrEnum

import imagehash
import numpy as np
import pdqhash
from PIL import Image
from pydantic import BaseModel

from ofmhelpers.repurpose.checker.normalize import dihedral, views
from ofmhelpers.repurpose.checker.sequence import SequenceMatch, best_line


class HashKind(StrEnum):
    PDQ = "pdq"
    PHASH = "phash"
    DHASH = "dhash"
    WHASH = "whash"


# Bits at or under which two frames count as the same picture.
SAME_PICTURE: dict[HashKind, int] = {
    HashKind.PDQ: 64,
    HashKind.PHASH: 12,
    HashKind.DHASH: 6,
    HashKind.WHASH: 6,
}

_IMAGEHASH: dict[HashKind, Callable[[Image.Image], imagehash.ImageHash]] = {
    HashKind.PHASH: imagehash.phash,
    HashKind.DHASH: imagehash.dhash,
    HashKind.WHASH: imagehash.whash,
}


class HashScore(BaseModel, frozen=True):
    """One hash's verdict over the copy's sampled frames."""

    kind: HashKind
    bits: int
    threshold: int
    # Per copy frame: bits to the closest source frame (any view / rotation).
    nearest: list[int]
    median: float
    minimum: int
    # Share of the copy's frames that find the same picture in the source,
    # in any order.
    matched_share: float
    # The same, only frames in order on one time line.
    sequence: SequenceMatch


def _packed(bits: np.ndarray) -> np.ndarray:
    return np.packbits(np.asarray(bits, dtype=bool).ravel())


def _pdq(image: Image.Image) -> np.ndarray:
    return np.ascontiguousarray(np.asarray(image.convert("RGB")))


def _stored(kind: HashKind, frame: Image.Image) -> np.ndarray:
    """(views, bytes): one frame's stored hashes."""
    if kind is HashKind.PDQ:
        return np.stack([_packed(pdqhash.compute(_pdq(v))[0]) for v in views(frame)])
    hasher = _IMAGEHASH[kind]
    return np.stack([_packed(hasher(v).hash) for v in views(frame)])


def _queried(kind: HashKind, frame: Image.Image) -> np.ndarray:
    """(views x 8, bytes): one frame's query hashes."""
    if kind is HashKind.PDQ:
        return np.stack(
            [
                _packed(h)
                for v in views(frame)
                for h in pdqhash.compute_dihedral(_pdq(v))[0]
            ]
        )
    hasher = _IMAGEHASH[kind]
    return np.stack(
        [_packed(hasher(d).hash) for v in views(frame) for d in dihedral(v)]
    )


def stored(kind: HashKind, source: list[Image.Image]) -> np.ndarray:
    """(source frames, views, bytes): what a source is looked up by."""
    return np.stack([_stored(kind, f) for f in source])


def distances(kind: HashKind, kept: np.ndarray, copy: list[Image.Image]) -> np.ndarray:
    """(copy frames, source frames): bits between each pair, the closest of
    every view / rotation pairing. `kept`: the source's `stored` hashes."""
    rows = []
    for frame in copy:
        query = _queried(kind, frame)  # (Q, bytes)
        xor = query[:, None, None, :] ^ kept[None, :, :, :]
        rows.append(np.bitwise_count(xor).sum(axis=-1).min(axis=(0, 2)))
    return np.stack(rows)


def score(
    kind: HashKind,
    kept: np.ndarray,
    source_t: np.ndarray,
    copy: list[Image.Image],
    copy_t: np.ndarray,
    sample_s: float,
) -> HashScore:
    """`kept`: the source's `stored` hashes, frames at `source_t` s; `copy`:
    normalized frames at `copy_t` s."""
    dist = distances(kind, kept, copy)
    threshold = SAME_PICTURE[kind]
    nearest = [int(d) for d in dist.min(axis=1)]
    return HashScore(
        kind=kind,
        bits=256 if kind is HashKind.PDQ else 64,
        threshold=threshold,
        nearest=nearest,
        median=statistics.median(nearest),
        minimum=min(nearest),
        matched_share=round(sum(d <= threshold for d in nearest) / len(nearest), 3),
        sequence=best_line(dist <= threshold, copy_t, source_t, sample_s),
    )
