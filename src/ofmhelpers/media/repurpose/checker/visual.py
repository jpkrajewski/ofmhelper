import statistics
from enum import StrEnum
lazy from collections.abc import Callable

import imagehash
import numpy as np
import pdqhash
from pydantic import BaseModel
lazy from PIL import Image

from ofmhelpers.media.repurpose.checker.normalize import dihedral, views
from ofmhelpers.media.repurpose.checker.sequence import SequenceMatch, best_line


class HashKind(StrEnum):
    PDQ = "pdq"
    PHASH = "phash"
    DHASH = "dhash"
    WHASH = "whash"


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
    kind: HashKind
    bits: int
    threshold: int
    nearest: list[int]
    median: float
    minimum: int
    matched_share: float
    sequence: SequenceMatch


def _packed(bits: np.ndarray) -> np.ndarray:
    return np.packbits(np.asarray(bits, dtype=bool).ravel())


def _pdq(image: Image.Image) -> np.ndarray:
    return np.ascontiguousarray(np.asarray(image.convert("RGB")))


def _stored(kind: HashKind, frame: Image.Image) -> np.ndarray:
    if kind is HashKind.PDQ:
        return np.stack([_packed(pdqhash.compute(_pdq(v))[0]) for v in views(frame)])
    hasher = _IMAGEHASH[kind]
    return np.stack([_packed(hasher(v).hash) for v in views(frame)])


def _queried(kind: HashKind, frame: Image.Image) -> np.ndarray:
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
    return np.stack([_stored(kind, f) for f in source])


def distances(kind: HashKind, kept: np.ndarray, copy: list[Image.Image]) -> np.ndarray:
    rows = []
    for frame in copy:
        query = _queried(kind, frame)
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
