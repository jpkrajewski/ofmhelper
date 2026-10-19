"""Shrink an image just enough to fit a provider's upload limits."""

from pathlib import Path

from PIL import Image, ImageOps
from pydantic import BaseModel, ConfigDict, Field

# Below this, a still-too-big image is downscaled instead of compressed further.
MIN_QUALITY = 80

QUALITY_STEP = 5

DOWNSCALE_STEP = 0.8

# Images `fitted` may re-encode (GIF is left alone: a JPEG copy would drop
# its animation).
FITTABLE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"})


class ImageLimits(BaseModel):
    """The largest reference image a provider takes."""

    model_config = ConfigDict(frozen=True)

    max_bytes: int = Field(gt=0)
    max_side: int = Field(gt=0)


def fits(src: Path, max_bytes: int, max_side: int) -> bool:
    """Whether `src` is already within both limits (header read only)."""
    if src.stat().st_size > max_bytes:
        return False
    with Image.open(src) as img:
        return max(img.size) <= max_side


def fit_image(src: Path, dest: Path, max_bytes: int, max_side: int) -> Path:
    """Write `src` to `dest` as a JPEG of at most `max_bytes` with no side
    over `max_side`, losing as little as possible: start at quality 95 / 4:4:4,
    step quality down to MIN_QUALITY, and only then shrink the pixels."""
    with Image.open(src) as opened:
        img = ImageOps.exif_transpose(opened).convert("RGB")
    img.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)

    quality = 95
    while True:
        img.save(dest, "JPEG", quality=quality, subsampling=0)
        if dest.stat().st_size <= max_bytes:
            return dest
        if quality > MIN_QUALITY:
            quality -= QUALITY_STEP
        else:
            img = img.resize(
                (int(img.width * DOWNSCALE_STEP), int(img.height * DOWNSCALE_STEP)),
                Image.Resampling.LANCZOS,
            )


def fitted(src: Path, dest_dir: Path, limits: ImageLimits) -> Path:
    """`src`, or a shrunk JPEG copy of it in `dest_dir` when it is an image
    over `limits`. The original stays untouched (a character sheet is kept
    lossless), and an existing copy is reused."""
    if src.suffix.lower() not in FITTABLE_SUFFIXES or fits(
        src, limits.max_bytes, limits.max_side
    ):
        return src
    dest = dest_dir / f"{src.stem}.jpg"
    if not dest.exists():
        dest_dir.mkdir(parents=True, exist_ok=True)
        fit_image(src, dest, limits.max_bytes, limits.max_side)
    return dest
