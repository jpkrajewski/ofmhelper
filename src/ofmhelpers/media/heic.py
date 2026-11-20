import io
from typing import BinaryIO

import pillow_heif

HEIC_EXTENSIONS = (".heic", ".heif")


def heic_to_jpeg(src: BinaryIO) -> io.BytesIO:
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
