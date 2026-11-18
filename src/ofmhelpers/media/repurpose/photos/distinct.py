"""Pictures that are not the same picture as Meta's own matcher sees it: PDQ
(`pdqhash`, the hash `checker/visual.py` uses), 256 bits; at or under
`SAME_PICTURE[PDQ]` bits apart = the same picture (calibrated there,
2026-10-26: 64). `pick`: the stills of a video that differ from each other;
`changed`: a still retouched (the preset's `Retouch`) until it differs from
the video's stills.

What moves PDQ, measured 2026-11-17 on a reel's 2268x4032 stills: the crop
(a share cut off each side, then back to the size): 2% ~30 bits, 4% ~55,
6% 60-100; brightness, contrast, colour and grain add ~2. The same as the
OOPSIE desktop spoofer does (crop, eq, noise, a hash check), owner
2026-11-17."""

import random

from PIL import Image, ImageEnhance
from pydantic import BaseModel
lazy import numpy as np
lazy import pdqhash

from ofmhelpers.log import get_logger
lazy from ofmhelpers.media.repurpose.checker.visual import SAME_PICTURE, HashKind
lazy from ofmhelpers.media.repurpose.photos.models import Retouch

logger = get_logger(__name__)


class Pdq(BaseModel, frozen=True):
    """A picture's PDQ hash (256 bits as one int) and PDQ's own quality
    (0-100: low = a flat, empty picture)."""

    bits: int
    quality: int


class Still(BaseModel, frozen=True):
    """A still of a video: when, and its hash."""

    at: float
    pdq: Pdq


def same_picture_bits() -> int:
    return SAME_PICTURE[HashKind.PDQ]


def pdq(picture: Image.Image, side: int) -> Pdq:
    """`picture`'s hash at short side `side`: every hash of a set at one
    size (the same frame read 12 bits apart at 256 and 2268 px)."""
    scale = side / min(picture.size)
    if scale < 1:
        size = (round(picture.width * scale), round(picture.height * scale))
        picture = picture.resize(size, Image.Resampling.LANCZOS)
    rgb = np.ascontiguousarray(np.asarray(picture.convert("RGB")))
    vector, quality = pdqhash.compute(rgb)
    bits = int.from_bytes(np.packbits(np.asarray(vector, dtype=bool)).tobytes())
    return Pdq(bits=bits, quality=int(quality))


def apart(a: Pdq, b: Pdq) -> int:
    """Bits that differ between two hashes."""
    return (a.bits ^ b.bits).bit_count()


def _nearest(still: Still, taken: list[Still]) -> int:
    return min(apart(still.pdq, t.pdq) for t in taken)


def pick(stills: list[Still], count: int) -> list[Still]:
    """Up to `count` stills no two of them the same picture, in time order:
    the clearest first (PDQ quality), then each time the one farthest from
    those taken. Fewer when the video has fewer different pictures (a reel
    ending on a still card gives one of it, not four)."""
    left = sorted(stills, key=lambda s: s.pdq.quality, reverse=True)
    if not left:
        return []
    taken = [left.pop(0)]
    while left and len(taken) < count:
        far = max(left, key=lambda s: (_nearest(s, taken), s.pdq.quality))
        if _nearest(far, taken) <= same_picture_bits():
            break
        taken.append(far)
        left.remove(far)
    return sorted(taken, key=lambda s: s.at)


def _retouched(
    picture: Image.Image, crop: float, how: Retouch, rng: random.Random
) -> Image.Image:
    """`picture` with a share of each side cut off (each side its own share
    of `crop`), back to its size, toned and grained."""
    width, height = picture.size
    left, top, right, bottom = (crop * rng.uniform(*how.side) for _ in range(4))
    box = (
        round(width * left),
        round(height * top),
        width - round(width * right),
        height - round(height * bottom),
    )
    out = picture.crop(box).resize((width, height), Image.Resampling.LANCZOS)
    for enhance in (ImageEnhance.Brightness, ImageEnhance.Contrast, ImageEnhance.Color):
        out = enhance(out).enhance(1 + rng.uniform(*how.tone))
    # float32: a 2268x4032 frame's float64 noise alone is 219 MB.
    noise = np.random.default_rng(rng.getrandbits(32)).standard_normal(
        (height, width, 3), dtype=np.float32
    )
    noise *= how.grain
    pixels = np.asarray(out, dtype=np.float32) + noise
    return Image.fromarray(np.clip(pixels, 0, 255).astype(np.uint8))


class Retouched(BaseModel, frozen=True, arbitrary_types_allowed=True):
    """A still made another picture: the picture, the crop it took, its hash
    and how far that is from the nearest of what it was compared with."""

    picture: Image.Image
    crop: float
    pdq: Pdq
    bits: int


def changed(
    picture: Image.Image,
    seen: list[Pdq],
    how: Retouch,
    side: int,
    rng: random.Random,
) -> Retouched:
    """`picture` retouched, one crop of `how.crops` a try, until PDQ finds
    it the same picture as none of `seen` (the video's stills and the
    pictures already made) nor `picture` itself (a moving camera's next
    frame can be closer than its own); the last try when none got there
    (logged, and its `bits` say so)."""
    frames = [pdq(picture, side), *seen]
    tried: Retouched | None = None
    for crop in how.crops:
        out = _retouched(picture, crop, how, rng)
        found = pdq(out, side)
        bits = min(apart(found, f) for f in frames)
        tried = Retouched(picture=out, crop=crop, pdq=found, bits=bits)
        if bits > same_picture_bits():
            return tried
    if tried is None:  # a preset has at least one crop (models.Retouch)
        msg = "a retouch with no crops"
        raise ValueError(msg)
    logger.warning(
        "photo still the same picture after the most crop: %d bits (limit %d)",
        tried.bits,
        same_picture_bits(),
    )
    return tried
