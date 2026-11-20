import os
import tempfile
from pathlib import Path

from PIL import Image, ImageOps

lazy from ofmhelpers.config.types import ImageLimits

MIN_QUALITY = 80

QUALITY_STEP = 5

DOWNSCALE_STEP = 0.8

FITTABLE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"})


def fits(src: Path, max_bytes: int, max_side: int) -> bool:
    if src.stat().st_size > max_bytes:
        return False
    with Image.open(src) as img:
        return max(img.size) <= max_side


def fit_image(src: Path, dest: Path, max_bytes: int, max_side: int) -> Path:
    with Image.open(src) as opened:
        img = ImageOps.exif_transpose(opened).convert("RGB")
    img.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)

    fd, name = tempfile.mkstemp(dir=dest.parent, prefix=f".{dest.stem}.", suffix=".jpg")
    os.close(fd)
    tmp = Path(name)
    try:
        quality = 95
        while True:
            img.save(tmp, "JPEG", quality=quality, subsampling=0)
            if tmp.stat().st_size <= max_bytes:
                break
            if quality > MIN_QUALITY:
                quality -= QUALITY_STEP
            else:
                img = img.resize(
                    (int(img.width * DOWNSCALE_STEP), int(img.height * DOWNSCALE_STEP)),
                    Image.Resampling.LANCZOS,
                )
        tmp.replace(dest)
    finally:
        tmp.unlink(missing_ok=True)
    return dest


def fitted(
    src: Path, dest_dir: Path, limits: ImageLimits, *, jpeg: bool = False
) -> Path:
    if src.suffix.lower() not in FITTABLE_SUFFIXES:
        return src
    if not jpeg and fits(src, limits.max_bytes, limits.max_side):
        return src
    dest = dest_dir / f"{src.stem}.jpg"
    if not dest.exists() or not fits(dest, limits.max_bytes, limits.max_side):
        dest_dir.mkdir(parents=True, exist_ok=True)
        fit_image(src, dest, limits.max_bytes, limits.max_side)
    return dest
