import hashlib
from pathlib import Path
lazy from collections.abc import Callable

import requests

from ofmhelpers.cache import delete_text, get_text, set_text
from ofmhelpers.config import settings
from ofmhelpers.integrations import outbound
from ofmhelpers.integrations.ai_providers.base import upload_lock
from ofmhelpers.log import get_logger
from ofmhelpers.media.image_fit import FITTABLE_SUFFIXES, ImageLimits, fitted
from ofmhelpers.media.video_mp4 import CONVERTIBLE_SUFFIXES, as_mp4

logger = get_logger(__name__)

_HTTP_ERROR = 400
COPYABLE_SUFFIXES = CONVERTIBLE_SUFFIXES | FITTABLE_SUFFIXES


def upload_cache_key(
    prefix: str, api_key: str, path: Path | str, max_bytes: int | None = None
) -> str:
    account = hashlib.sha256(api_key.encode()).hexdigest()[:16]
    key = f"{prefix}:upload:{account}:{path}"
    return f"{key}:{max_bytes}" if max_bytes else key


def memoized(
    cache_key: str,
    ttl_s: int,
    upload: Callable[[], str],
    live: Callable[[str], bool] | None = None,
) -> str:
    with upload_lock(cache_key):
        cached = get_text(cache_key)
        if cached is not None:
            if live is None or live(cached):
                logger.info("upload cache hit: %s", cache_key)
                return cached
            logger.info("cached upload %s no longer resolves", cache_key)
            delete_text(cache_key)
        uploaded = upload()
        set_text(cache_key, uploaded, ttl_s)
        return uploaded


def remote_file_exists(url: str, timeout_s: float) -> bool:
    try:
        response = outbound.head(url, timeout=timeout_s, allow_redirects=True)
    except requests.RequestException:
        logger.warning("uploaded file not checked: uploading again", exc_info=True)
        return False
    return response.status_code < _HTTP_ERROR


def reference_copy(
    path: Path,
    folder: str,
    limits: ImageLimits,
    convert_timeout_s: int,
    takes: Callable[[Path], bool],
) -> Path:
    out = Path(settings.aigen.out_dir) / folder
    suffix = path.suffix.lower()
    if suffix in CONVERTIBLE_SUFFIXES and not takes(path):
        return as_mp4(path, out / "mp4", convert_timeout_s)
    jpeg = suffix in FITTABLE_SUFFIXES and not takes(path)
    return fitted(path, out / "fitted", limits, jpeg=jpeg)
