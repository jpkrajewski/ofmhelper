# Copyright (C) 2026 jpkrajewski

import statistics
lazy from collections.abc import Callable

import imagehash
import numpy as np
import pdqhash
lazy from PIL import Image

from ofmhelpers.media.repurpose.checker.normalize import dihedral, views
from ofmhelpers.media.repurpose.checker.schema import HashKind, HashScore
from ofmhelpers.media.repurpose.checker.sequence import best_line

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
            ],
        )
    hasher = _IMAGEHASH[kind]
    return np.stack(
        [_packed(hasher(d).hash) for v in views(frame) for d in dihedral(v)],
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


def score(  # noqa: PLR0913, PLR0917
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
