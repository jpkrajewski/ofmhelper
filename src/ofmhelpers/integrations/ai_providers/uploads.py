"""What every provider's reference upload shares: the per-account memo in Redis,
one upload per file across workers (`base.upload_lock`), the liveness check of a
remembered URL, and the copy of a file the provider does not take.

The memo is pure optimisation: a broker outage just means the file goes up again.
"""

import hashlib
from collections.abc import Callable
from pathlib import Path

import requests

from ofmhelpers.cache import delete_text, get_text, set_text
from ofmhelpers.config import settings
from ofmhelpers.integrations.ai_providers.base import upload_lock
from ofmhelpers.log import get_logger
from ofmhelpers.media.image_fit import FITTABLE_SUFFIXES, ImageLimits, fitted
from ofmhelpers.media.video_mp4 import CONVERTIBLE_SUFFIXES, as_mp4
from ofmhelpers.utils import outbound

logger = get_logger(__name__)

_HTTP_ERROR = 400
# What `reference_copy` can turn into something a provider takes.
COPYABLE_SUFFIXES = CONVERTIBLE_SUFFIXES | FITTABLE_SUFFIXES


def upload_cache_key(
    prefix: str, api_key: str, path: Path | str, max_bytes: int | None = None
) -> str:
    """Per account (uploads are), without the key itself: this names a Redis key
    and a lock, and is logged when the lock fails. Carries a model's own limit:
    under it a file may go up untouched, never the copy cached under the default
    cap."""
    account = hashlib.sha256(api_key.encode()).hexdigest()[:16]
    key = f"{prefix}:upload:{account}:{path}"
    return f"{key}:{max_bytes}" if max_bytes else key


def memoized(
    cache_key: str,
    ttl_s: int,
    upload: Callable[[], str],
    live: Callable[[str], bool] | None = None,
) -> str:
    """What `upload` answered, once per file while remembered: requests that
    start together wait here and take the first one's answer. `live` confirms a
    remembered answer before it is reused; a dead one is uploaded again."""
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
    """Best-effort check of a previously uploaded file's URL. Fails closed: any
    doubt means upload again rather than hand a provider a broken reference."""
    try:
        response = outbound.head(url, timeout=timeout_s, allow_redirects=True)
    except requests.RequestException:
        return False
    return response.status_code < _HTTP_ERROR


def reference_copy(
    path: Path,
    folder: str,
    limits: ImageLimits,
    convert_timeout_s: int,
    takes: Callable[[Path], bool],
) -> Path:
    """`path`, or the copy of it (in `out_dir/<folder>`) a provider takes: a video
    in a container it does not `takes` as an MP4, an image in such a format or
    over `limits` as a JPEG. The original stays untouched. Errors are the
    caller's to word (`as_mp4`, `fitted`)."""
    out = Path(settings.aigen.out_dir) / folder
    suffix = path.suffix.lower()
    if suffix in CONVERTIBLE_SUFFIXES and not takes(path):
        return as_mp4(path, out / "mp4", convert_timeout_s)
    jpeg = suffix in FITTABLE_SUFFIXES and not takes(path)
    return fitted(path, out / "fitted", limits, jpeg=jpeg)
