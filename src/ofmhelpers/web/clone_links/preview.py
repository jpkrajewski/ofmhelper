"""Generate's look at a link before anything is downloaded or paid: yt-dlp
reads the video's length and cover (`downloaders.generic.video_info`, no
download, as the clicker with their own cookies.txt), the cover is fetched
once and kept small (`reel_thumbs.kept_thumb`, served from disk only by
`/clone-links/thumb/{key}`); the read itself (length, cover URL) cached in
Redis per user and link; the length priced on every look.

Bounded: one request ends within `preview_deadline_s` (a link not read by
then is "took too long"), and the whole process reads at most
`preview_parallel` links at once (`_read_slots`), however many requests
stack up (Cancel + Generate again, two VAs)."""

from __future__ import annotations

import hashlib
import math
import re
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from functools import lru_cache
from typing import TYPE_CHECKING

from pydantic import BaseModel

from ofmhelpers.cache import delete_text, get_text, set_text
from ofmhelpers.config import settings
from ofmhelpers.downloaders.cookies import cookie_file
from ofmhelpers.downloaders.generic import video_info
from ofmhelpers.downloaders.models import VideoInfo
from ofmhelpers.log import get_logger
from ofmhelpers.web.clone_links.links import site_name
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
TOO_SLOW = "Took too long, try again"
# yt-dlp's words for a login the site wants, or a login it refused:
# `raise_login_required` appends its "--cookies" hint; expired Instagram
# cookies answer a bare HTTP 400 (prod, downloaders/CLAUDE.md).
_LOGIN_WORDS = ("login", "log in", "sign in", "cookies", "no longer valid")
_AUTH_REFUSED = ("http error 400", "http error 401")
# A site turning requests away for a while (a login gets past the
# anonymous limit), or not answering at all.
_THROTTLED = ("http error 429", "too many requests", "rate-limit", "rate limit")
_NO_ANSWER = ("timed out", "connection", "temporarily unavailable")


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


def needs_login(url: str) -> str:
    return f"{site_name(url)} wants a login: upload your cookies on the Cookies page"


def busy(url: str) -> str:
    return f"{site_name(url)} is busy, try again in a few minutes"


def _says(error: str | None, words: tuple[str, ...]) -> bool:
    lowered = (error or "").lower()
    return any(word in lowered for word in words)


def _why(info: VideoInfo, has_cookies: bool, url: str) -> str:
    """Why `info` could not be read, as the VA can act on it. `info.error`
    is the last try's answer (the login, when there is one),
    `logged_out_error` the first logged-out try's when that differed.
    Without cookies: a login wall or a rate limit on any try = upload
    cookies. With cookies (the last try used them): refused = upload fresh
    ones (expired ones answer a bare 400), throttled = wait. No answer =
    wait. Anything else (removed, private, no video) = can't open."""
    last = info.error
    if not has_cookies:
        tries = (last, info.logged_out_error)
        if any(_says(e, _LOGIN_WORDS + _THROTTLED) for e in tries):
            return needs_login(url)
        return busy(url) if any(_says(e, _NO_ANSWER) for e in tries) else CANNOT_OPEN
    if _says(last, _LOGIN_WORDS + _AUTH_REFUSED):
        return needs_login(url)
    if _says(last, _THROTTLED + _NO_ANSWER):
        return busy(url)
    return CANNOT_OPEN


def _info_key(key: str, user_id: int | None) -> str:
    """Per user: a read only the clicker's cookies could make is theirs."""
    return f"clone-links:info:{user_id or 0}:{key}"


def _remembered(info_key: str) -> VideoInfo | None:
    """The link's yt-dlp read from Redis; None on a miss (or Redis down)."""
    raw = get_text(info_key)
    if raw is None:
        return None
    try:
        return VideoInfo.model_validate_json(raw)
    except ValueError:
        logger.warning(
            "clone-links: cached info %s unreadable", info_key, exc_info=True
        )
        return None


@lru_cache(maxsize=1)
def _read_slots() -> threading.BoundedSemaphore:
    """The process's preview reads in flight, across every request."""
    return threading.BoundedSemaphore(settings.clone_links.preview_parallel)


def _read(url: str, cookies: str | None, until: float | None) -> VideoInfo | None:
    """yt-dlp's read of `url` once a read slot is free; None when none was
    before `until` (the request's deadline, `time.monotonic()`)."""
    slots = _read_slots()
    wait = None if until is None else max(0.0, until - time.monotonic())
    if not slots.acquire(timeout=wait):
        return None
    try:
        with cookie_file(cookies) as cookiefile:
            return video_info(
                url,
                cookiefile,
                socket_timeout=settings.clone_links.preview_socket_timeout_s,
            )
    finally:
        slots.release()


def _too_long(seconds: float, max_s: int) -> bool:
    """A whole-second length (TikTok's, rounded down) says nothing about
    the fraction: at the limit it may be 15.4 s, which the worker's own
    measure refuses."""
    if float(seconds).is_integer():
        return seconds >= max_s
    return seconds > max_s


def _shown(seconds: float, too_long: bool, max_s: int) -> float | None:
    """The length on the tile, rounded UP to 0.1 s (15.04 s is "15.1 s",
    never "15.0 s" next to Too long); None when even that would read as
    fitting (a whole 15 s, refused as maybe 15.x)."""
    shown = math.ceil(round(seconds * 10, 6)) / 10
    return None if too_long and shown <= max_s else shown


def preview(
    url: str,
    user_id: int | None,
    price: Price,
    thumbs: str,
    *,
    until: float | None = None,
) -> LinkPreview:
    """`url`'s video as Generate's dialog lists it (`thumbs`: the route
    prefix its cover is served under). yt-dlp reads a link once per user:
    its length and cover URL are cached in Redis (`OFM_CLONE_LINKS_INFO_TTL_S`,
    any reload), a failed read is not; a cached read whose cover can no
    longer be fetched (the signed URL expired) is forgotten. The price is
    worked out on every call (the rate may have changed). Never raises for
    the link. `until`: the request's deadline (`time.monotonic()`)."""
    key = thumb_key(url)
    info_key = _info_key(key, user_id)
    info = _remembered(info_key)
    cached = info is not None
    if info is None:
        cookies = login_cookies.for_user(user_id)
        info = _read(url, cookies, until)
        if info is None:
            return LinkPreview(url=url, max_s=price.max_s, error=TOO_SLOW)
        if info.error or info.seconds is None:
            logger.info(
                "clone-links preview %s: %s (logged out: %s)",
                url,
                info.error or "no length",
                info.logged_out_error,
            )
            error = _why(info, cookies is not None, url)
            return LinkPreview(url=url, max_s=price.max_s, error=error)
        set_text(info_key, info.model_dump_json(), settings.clone_links.info_ttl_s)
    seconds = info.seconds or 0.0
    cover = thumb_file(key)
    fetched = cover.is_file() or not info.thumbnail or kept_thumb(cover, info.thumbnail)
    if not fetched and cached:
        delete_text(info_key)
    too_long = _too_long(seconds, price.max_s)
    spent = seconds * price.credits_per_s
    return LinkPreview(
        url=url,
        seconds=_shown(seconds, too_long, price.max_s),
        thumb_url=f"{thumbs}/{key}" if cover.is_file() else None,
        max_s=price.max_s,
        too_long=too_long,
        credits=round(spent, 1),
        usd=round(spent * price.usd_per_credit, 2),
    )


def _answer(done: Future[LinkPreview], url: str, price: Price) -> LinkPreview:
    """A finished preview's answer; one that raised = can't open (logged)."""
    try:
        return done.result()
    except Exception:
        logger.warning("clone-links preview %s failed", url, exc_info=True)
        return LinkPreview(url=url, max_s=price.max_s, error=CANNOT_OPEN)


def preview_all(
    urls: list[str], user_id: int | None, price: Price, thumbs: str
) -> Iterator[LinkPreview]:
    """Every link's preview, each the moment it is done (cached ones at
    once), read `settings.clone_links.preview_parallel` at a time (and no
    more across the process). By `preview_deadline_s` every link has an
    answer: one not read yet is `TOO_SLOW`. Closing the generator (the page
    went away) drops the ones not started."""
    deadline_s = settings.clone_links.preview_deadline_s
    until = time.monotonic() + deadline_s
    pool = ThreadPoolExecutor(max_workers=settings.clone_links.preview_parallel)
    futures = {
        pool.submit(preview, url, user_id, price, thumbs, until=until): url
        for url in urls
    }
    answered: set[Future[LinkPreview]] = set()
    try:
        try:
            for done in as_completed(futures, timeout=deadline_s):
                answered.add(done)
                yield _answer(done, futures[done], price)
        except TimeoutError:
            late = [f for f in futures if f not in answered]
            logger.warning(
                "clone-links preview: %d of %d links not read in %d s",
                sum(not f.done() for f in late),
                len(futures),
                deadline_s,
            )
            for future in late:
                url = futures[future]
                if future.done():
                    yield _answer(future, url, price)
                else:
                    yield LinkPreview(url=url, max_s=price.max_s, error=TOO_SLOW)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
