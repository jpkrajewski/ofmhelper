"""Generate's look at a link before anything is downloaded or paid: yt-dlp
reads the video's length and cover (`downloaders.generic.video_info`, no
download, as the clicker with their own cookies.txt), the cover is fetched
once and kept small (`reel_thumbs.kept_thumb`, served from disk only by
`/clone-links/thumb/{key}`); the read itself (length, cover URL) cached in
Redis per link; the length priced on every look."""

from __future__ import annotations

import hashlib
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TYPE_CHECKING

from pydantic import BaseModel

from ofmhelpers.cache import get_text, set_text
from ofmhelpers.config import settings
from ofmhelpers.downloaders.cookies import cookie_file
from ofmhelpers.downloaders.generic import video_info
from ofmhelpers.downloaders.models import VideoInfo
from ofmhelpers.log import get_logger
from ofmhelpers.web.clone_links.models import LinkPreview
from ofmhelpers.web.previews import previews_dir
from ofmhelpers.web.reel_thumbs import kept_thumb
from ofmhelpers.web.stores import cookies as login_cookies

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

logger = get_logger(__name__)

THUMB_KEY = re.compile(r"^[0-9a-f]{24}$")
CANNOT_OPEN = "Can't open this video"
NEEDS_LOGIN = "Instagram wants a login: upload your cookies on the Cookies page"


class Price(BaseModel):
    """What a second of source video costs at the picked provider and
    resolution, and the longest one it takes."""

    credits_per_s: float
    usd_per_credit: float
    max_s: int


def thumb_key(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:24]


def thumb_file(key: str) -> Path:
    return previews_dir() / "clone-links" / f"{key}.webp"


def _why(error: str) -> str:
    lowered = error.lower()
    if "login" in lowered or "cookies" in lowered:
        return NEEDS_LOGIN
    return CANNOT_OPEN


def _info_key(key: str) -> str:
    return f"clone-links:info:{key}"


def _remembered(key: str) -> VideoInfo | None:
    """The link's yt-dlp read from Redis; None on a miss (or Redis down)."""
    raw = get_text(_info_key(key))
    if raw is None:
        return None
    try:
        return VideoInfo.model_validate_json(raw)
    except ValueError:
        logger.warning("clone-links: cached info %s unreadable", key, exc_info=True)
        return None


def preview(url: str, user_id: int | None, price: Price, thumbs: str) -> LinkPreview:
    """`url`'s video as Generate's dialog lists it (`thumbs`: the route
    prefix its cover is served under). yt-dlp reads a link once: its length
    and cover URL are cached in Redis (`OFM_CLONE_LINKS_INFO_TTL_S`, any
    user, any reload), a failed read is not. The price is worked out on
    every call (the rate may have changed). Never raises for the link."""
    key = thumb_key(url)
    info = _remembered(key)
    if info is None:
        with cookie_file(login_cookies.for_user(user_id)) as cookiefile:
            info = video_info(url, cookiefile)
        if info.error or info.seconds is None:
            logger.info("clone-links preview %s: %s", url, info.error)
            error = info.error or "No length"
            return LinkPreview(
                url=url, max_s=price.max_s, error=_why(error), error_detail=error
            )
        set_text(
            _info_key(key), info.model_dump_json(), settings.clone_links.info_ttl_s
        )
    seconds = info.seconds or 0.0
    cover = thumb_file(key)
    if not cover.is_file() and info.thumbnail:
        kept_thumb(cover, info.thumbnail)
    spent = seconds * price.credits_per_s
    return LinkPreview(
        url=url,
        seconds=round(seconds, 1),
        thumb_url=f"{thumbs}/{key}" if cover.is_file() else None,
        max_s=price.max_s,
        too_long=seconds > price.max_s,
        credits=round(spent, 1),
        usd=round(spent * price.usd_per_credit, 2),
    )


def preview_all(
    urls: list[str], user_id: int | None, price: Price, thumbs: str
) -> Iterator[LinkPreview]:
    """Every link's preview, each the moment it is done (cached ones at
    once): read `settings.clone_links.preview_parallel` at a time. Closing
    the generator (the page went away) drops the ones not started."""
    pool = ThreadPoolExecutor(max_workers=settings.clone_links.preview_parallel)
    futures = {pool.submit(preview, url, user_id, price, thumbs): url for url in urls}
    try:
        for done in as_completed(futures):
            try:
                yield done.result()
            except Exception as exc:
                url = futures[done]
                logger.warning("clone-links preview %s failed", url, exc_info=True)
                yield LinkPreview(
                    url=url, max_s=price.max_s, error=CANNOT_OPEN, error_detail=str(exc)
                )
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
