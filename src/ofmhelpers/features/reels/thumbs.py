import io
import ipaddress
import socket
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import requests
from PIL import Image, UnidentifiedImageError

from ofmhelpers.config import settings
from ofmhelpers.features.previews.render import previews_dir, write_atomic
from ofmhelpers.integrations import outbound
from ofmhelpers.log import get_logger

logger = get_logger(__name__)

_MAX_REDIRECTS = 3


class NotPublicError(requests.RequestException): ...


def thumb_path(code: str) -> Path:
    return previews_dir() / "reels" / f"{code}_{settings.instagram.feed_thumb_px}.webp"


def _shrink(content: bytes, dest: Path) -> None:
    size = settings.instagram.feed_thumb_px
    with Image.open(io.BytesIO(content)) as img:
        img.thumbnail((size, size * 2))
        img.convert("RGB").save(dest, "WEBP", quality=80)


def reel_thumb(code: str, source_url: str) -> Path | None:
    return kept_thumb(thumb_path(code), source_url)


def _addresses(host: str) -> list[str]:
    return [str(info[4][0]) for info in socket.getaddrinfo(host, None)]


def _check_public(url: str) -> None:
    if settings.infra.fake_externals:
        return
    parts = urlsplit(url)
    host = parts.hostname or ""
    try:
        found = _addresses(host) if parts.scheme in ("http", "https") and host else []
        public = bool(found) and all(
            ipaddress.ip_address(a.split("%")[0]).is_global for a in found
        )
    except OSError, ValueError:  # silent: unresolvable = not public (fails closed)
        public = False
    if not public:
        msg = f"cover host {host!r} is not on the public internet"
        raise NotPublicError(msg)


def _fetch(url: str) -> requests.Response:
    for _hop in range(_MAX_REDIRECTS + 1):
        _check_public(url)
        response = outbound.get(
            url, timeout=settings.instagram.timeout_s, allow_redirects=False
        )
        if not response.is_redirect:
            response.raise_for_status()
            return response
        url = urljoin(url, response.headers["location"])
    msg = f"more than {_MAX_REDIRECTS} redirects"
    raise requests.TooManyRedirects(msg)


def kept_thumb(dest: Path, source_url: str) -> Path | None:
    if dest.is_file():
        return dest
    try:
        response = _fetch(source_url)
        write_atomic(dest, lambda tmp: _shrink(response.content, tmp))
    except (requests.RequestException, UnidentifiedImageError, OSError) as exc:
        logger.warning("thumbnail %s not fetched: %s", dest.name, exc, exc_info=True)
        return None
    return dest
