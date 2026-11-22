lazy import random

from PIL import Image, ImageEnhance
from pydantic import BaseModel
lazy import numpy as np
lazy import pdqhash

from ofmhelpers.log import get_logger
lazy from ofmhelpers.media.repurpose.checker.schema import HashKind
lazy from ofmhelpers.media.repurpose.checker.visual import SAME_PICTURE
lazy from ofmhelpers.media.repurpose.photos.models import Retouch

logger = get_logger(__name__)


class Pdq(BaseModel, frozen=True):
    bits: int
    quality: int


class Still(BaseModel, frozen=True):
    at: float
    pdq: Pdq


def same_picture_bits() -> int:
    return SAME_PICTURE[HashKind.PDQ]


def pdq(picture: Image.Image, side: int) -> Pdq:
    scale = side / min(picture.size)
    if scale < 1:
        size = (round(picture.width * scale), round(picture.height * scale))
        picture = picture.resize(size, Image.Resampling.LANCZOS)
    rgb = np.ascontiguousarray(np.asarray(picture.convert("RGB")))
    vector, quality = pdqhash.compute(rgb)
    bits = int.from_bytes(np.packbits(np.asarray(vector, dtype=bool)).tobytes())
    return Pdq(bits=bits, quality=int(quality))


def apart(a: Pdq, b: Pdq) -> int:
    return (a.bits ^ b.bits).bit_count()


def _nearest(still: Still, taken: list[Still]) -> int:
    return min(apart(still.pdq, t.pdq) for t in taken)


def pick(stills: list[Still], count: int) -> list[Still]:
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
    noise = np.random.default_rng(rng.getrandbits(32)).standard_normal(
        (height, width, 3), dtype=np.float32
    )
    noise *= how.grain
    pixels = np.asarray(out, dtype=np.float32) + noise
    return Image.fromarray(np.clip(pixels, 0, 255).astype(np.uint8))


class Retouched(BaseModel, frozen=True, arbitrary_types_allowed=True):
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
    frames = [pdq(picture, side), *seen]
    tried: Retouched | None = None
    for crop in how.crops:
        out = _retouched(picture, crop, how, rng)
        found = pdq(out, side)
        bits = min(apart(found, f) for f in frames)
        tried = Retouched(picture=out, crop=crop, pdq=found, bits=bits)
        if bits > same_picture_bits():
            return tried
    if tried is None:
        msg = "a retouch with no crops"
        raise ValueError(msg)
    logger.warning(
        "photo still the same picture after the most crop: %d bits (limit %d)",
        tried.bits,
        same_picture_bits(),
    )
    return tried
