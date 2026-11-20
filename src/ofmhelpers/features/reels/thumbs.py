import io
from pathlib import Path

import requests
from PIL import Image, UnidentifiedImageError

from ofmhelpers.config import settings
from ofmhelpers.features.previews.render import previews_dir, write_atomic
from ofmhelpers.integrations import egress
from ofmhelpers.log import get_logger

logger = get_logger(__name__)


def thumb_path(code: str) -> Path:
    return previews_dir() / "reels" / f"{code}_{settings.instagram.feed_thumb_px}.webp"


def _shrink(content: bytes, dest: Path) -> None:
    size = settings.instagram.feed_thumb_px
    with Image.open(io.BytesIO(content)) as img:
        img.thumbnail((size, size * 2))
        img.convert("RGB").save(dest, "WEBP", quality=80)


def reel_thumb(code: str, source_url: str) -> Path | None:
    return kept_thumb(thumb_path(code), source_url)


def kept_thumb(dest: Path, source_url: str) -> Path | None:
    if dest.is_file():
        return dest
    try:
        response = egress.get(source_url, timeout=settings.instagram.timeout_s)
        write_atomic(dest, lambda tmp: _shrink(response.content, tmp))
    except (requests.RequestException, UnidentifiedImageError, OSError) as exc:
        logger.warning("thumbnail %s not fetched: %s", dest.name, exc, exc_info=True)
        return None
    return dest
