"""Small, kept copies of /reels' thumbnails.

Instagram's CDN serves a reel's cover at full size (64-284 KB each, prod
2026-10-22): a feed page of them was megabytes before anything played, and
its signed URLs expire (`oe=`), leaving old cards blank. `GET
/reels/thumb/{code}` serves this instead: fetched once (`outbound.get`, so the
fakes answer under OFM_FAKE_EXTERNALS), shrunk to `feed_thumb_px` webp (a few
KB) and kept under `<uploads>/.previews/reels/`, so every later view is a
file read.

A cover URL comes from the page yt-dlp read (/clone-links: any site's), so
it is fetched only from a host on the public internet: every address the
host resolves to global, checked again on each redirect (never followed
blindly), so a crafted page cannot point the server at postgres:5432 or
169.254.169.254.
"""

import io
import ipaddress
import socket
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import requests
from PIL import Image, UnidentifiedImageError

from ofmhelpers.config import settings
from ofmhelpers.log import get_logger
from ofmhelpers.utils import outbound
from ofmhelpers.web.previews import previews_dir, write_atomic

logger = get_logger(__name__)

# Redirects one cover fetch follows (a CDN's own hop or two).
_MAX_REDIRECTS = 3


class NotPublicError(requests.RequestException):
    """The cover's host is not on the public internet (or does not resolve)."""


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


def _addresses(host: str) -> list[str]:
    """Every address `host` resolves to (`OSError` when it does not)."""
    return [str(info[4][0]) for info in socket.getaddrinfo(host, None)]


def _check_public(url: str) -> None:
    """Raises `NotPublicError` unless `url` is http(s) to a host whose every
    address is global. Under OFM_FAKE_EXTERNALS nothing leaves the process
    (`outbound`'s fakes answer), so there is no address to check."""
    if settings.infra.fake_externals:
        return
    parts = urlsplit(url)
    host = parts.hostname or ""
    try:
        found = _addresses(host) if parts.scheme in ("http", "https") and host else []
        public = bool(found) and all(
            ipaddress.ip_address(a.split("%")[0]).is_global for a in found
        )
    except (OSError, ValueError):
        public = False
    if not public:
        msg = f"cover host {host!r} is not on the public internet"
        raise NotPublicError(msg)


def _fetch(url: str) -> requests.Response:
    """GET `url`, each redirect's target checked like the first host."""
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
    """`source_url`'s image shrunk into `dest`, fetched only when `dest` is
    missing (any site's cover: /clone-links' previews too), only from a
    public host; None when it cannot be fetched or is not an image."""
    if dest.is_file():
        return dest
    try:
        response = _fetch(source_url)
        write_atomic(dest, lambda tmp: _shrink(response.content, tmp))
    except (requests.RequestException, UnidentifiedImageError, OSError) as exc:
        logger.warning("thumbnail %s not fetched: %s", dest.name, exc)
        return None
    return dest
