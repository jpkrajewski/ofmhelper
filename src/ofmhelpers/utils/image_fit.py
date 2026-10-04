"""Shrink an image just enough to fit a provider's upload limits."""

from pathlib import Path

from PIL import Image, ImageOps

# Below this, a still-too-big image is downscaled instead of compressed further.
MIN_QUALITY = 80

QUALITY_STEP = 5

DOWNSCALE_STEP = 0.8


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
