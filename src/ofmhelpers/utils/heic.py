"""HEIC/HEIF (iPhone photo) to JPEG.

Nothing downstream speaks HEIC -- not the browser previews, not the kie.ai
models, not the metadata cleaner -- so an iPhone photo is converted once, at
upload, and the rest of the app only ever sees a JPEG.
"""

from __future__ import annotations

import io
from typing import BinaryIO

import pillow_heif

HEIC_EXTENSIONS = (".heic", ".heif")


def heic_to_jpeg(src: BinaryIO) -> io.BytesIO:
    """Decode a HEIC/HEIF stream and re-encode it as the best JPEG Pillow can
    write: quality 100, no chroma subsampling (4:4:4), ICC profile and EXIF
    carried over. libheif has already applied the rotation, and pillow-heif
    resets the EXIF orientation to match, so the result is upright as-is.

    JPEG rather than PNG: the source is already lossy, so q100/4:4:4 is
    visually lossless at a fraction of a lossless PNG's size, which matters
    for providers with a per-image size cap."""
    img = pillow_heif.open_heif(src, convert_hdr_to_8bit=True).to_pillow()
    out = io.BytesIO()
    img.convert("RGB").save(
        out,
        "JPEG",
        quality=100,
        subsampling=0,
        icc_profile=img.info.get("icc_profile"),
        exif=img.info.get("exif") or b"",
    )
    out.seek(0)
    return out
