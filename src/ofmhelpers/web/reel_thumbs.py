"""Small, kept copies of /reels' thumbnails.

Instagram's CDN serves a reel's cover at full size (64-284 KB each, prod
2026-10-22): a feed page of them was megabytes before anything played, and
its signed URLs expire (`oe=`), leaving old cards blank. `GET
/reels/thumb/{code}` serves this instead: fetched once (`outbound.get`, so the
fakes answer under OFM_FAKE_EXTERNALS), shrunk to `feed_thumb_px` webp (a few
KB) and kept under `<uploads>/.previews/reels/`, so every later view is a
file read.
"""

import io
from pathlib import Path

import requests
from PIL import Image, UnidentifiedImageError

from ofmhelpers.config import settings
from ofmhelpers.log import get_logger
from ofmhelpers.utils import outbound
from ofmhelpers.web.previews import previews_dir, write_atomic

logger = get_logger(__name__)


def thumb_path(code: str) -> Path:
    return previews_dir() / "reels" / f"{code}_{settings.instagram.feed_thumb_px}.webp"


def _shrink(content: bytes, dest: Path) -> None:
    size = settings.instagram.feed_thumb_px
    with Image.open(io.BytesIO(content)) as img:
        img.thumbnail((size, size * 2))
        img.convert("RGB").save(dest, "WEBP", quality=80)


def reel_thumb(code: str, source_url: str) -> Path | None:
    """The kept thumbnail, fetched and shrunk first when missing; None when
    Instagram no longer serves it (expired URL, 403) or it is not an image."""
    return kept_thumb(thumb_path(code), source_url)


def kept_thumb(dest: Path, source_url: str) -> Path | None:
    """`source_url`'s image shrunk into `dest`, fetched only when `dest` is
    missing (any site's cover: /clone-links' previews too); None when it
    cannot be fetched or is not an image."""
    if dest.is_file():
        return dest
    try:
        response = outbound.get(source_url, timeout=settings.instagram.timeout_s)
        response.raise_for_status()
        write_atomic(dest, lambda tmp: _shrink(response.content, tmp))
    except (requests.RequestException, UnidentifiedImageError, OSError) as exc:
        logger.warning("thumbnail %s not fetched: %s", dest.name, exc)
        return None
    return dest
