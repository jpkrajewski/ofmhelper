# Copyright (C) 2026 jpkrajewski

import hashlib
import math
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from functools import lru_cache
lazy from collections.abc import Iterator
lazy from pathlib import Path

from pydantic import BaseModel

from ofmhelpers.cache import delete_text, get_text, set_text
from ofmhelpers.config import settings
from ofmhelpers.db.cookies import store as login_cookies
from ofmhelpers.features.clone_links.constants import clone_links_constants
from ofmhelpers.features.clone_links.links import site_name
from ofmhelpers.features.clone_links.models import LinkPreview
from ofmhelpers.features.previews.render import previews_dir
from ofmhelpers.features.reels.thumbs import kept_thumb
from ofmhelpers.integrations.cookie_jar import cookie_file
from ofmhelpers.integrations.downloaders.generic import video_info
from ofmhelpers.integrations.downloaders.models import VideoInfo
from ofmhelpers.log import get_logger
from ofmhelpers.monitoring.telemetry import in_this_trace

logger = get_logger(__name__)


class Price(BaseModel):
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
    constants = clone_links_constants()
    last = info.error
    if not has_cookies:
        tries = (last, info.logged_out_error)
        if any(
            _says(e, constants.login_words + constants.throttled_words) for e in tries
        ):
            return needs_login(url)
        if any(_says(e, constants.no_answer_words) for e in tries):
            return busy(url)
        return constants.cannot_open
    if _says(last, constants.login_words + constants.auth_refused_words):
        return needs_login(url)
    if _says(last, constants.throttled_words + constants.no_answer_words):
        return busy(url)
    return constants.cannot_open


def _info_key(key: str, user_id: int | None) -> str:
    return f"clone-links:info:{user_id or 0}:{key}"


def previewed(key: str, user_id: int | None) -> bool:
    return get_text(_info_key(key, user_id)) is not None


def _remembered(info_key: str) -> VideoInfo | None:
    raw = get_text(info_key)
    if raw is None:
        return None
    try:
        return VideoInfo.model_validate_json(raw)
    except ValueError:
        logger.warning(
            "clone-links: cached info %s unreadable",
            info_key,
            exc_info=True,
        )
        return None


@lru_cache(maxsize=1)
def _read_slots() -> threading.BoundedSemaphore:
    return threading.BoundedSemaphore(settings.clone_links.preview_parallel)


def _read(url: str, cookies: str | None, until: float | None) -> VideoInfo | None:
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
    if float(seconds).is_integer():
        return seconds >= max_s
    return seconds > max_s


def _shown(seconds: float, too_long: bool, max_s: int) -> float | None:
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
    key = thumb_key(url)
    info_key = _info_key(key, user_id)
    info = _remembered(info_key)
    cached = info is not None
    if info is None:
        cookies = login_cookies.for_user(user_id)
        info = _read(url, cookies, until)
        if info is None:
            return LinkPreview(
                url=url,
                max_s=price.max_s,
                error=clone_links_constants().too_slow,
            )
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
    try:
        return done.result()
    except Exception:
        logger.warning("clone-links preview %s failed", url, exc_info=True)
        return LinkPreview(
            url=url,
            max_s=price.max_s,
            error=clone_links_constants().cannot_open,
        )


def preview_all(
    urls: list[str],
    user_id: int | None,
    price: Price,
    thumbs: str,
) -> Iterator[LinkPreview]:
    deadline_s = settings.clone_links.preview_deadline_s
    until = time.monotonic() + deadline_s
    pool = ThreadPoolExecutor(max_workers=settings.clone_links.preview_parallel)
    futures = {
        pool.submit(
            in_this_trace(
                lambda link: preview(link, user_id, price, thumbs, until=until),
            ),
            url,
        ): url
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
                exc_info=True,
            )
            for future in late:
                url = futures[future]
                if future.done():
                    yield _answer(future, url, price)
                else:
                    yield LinkPreview(
                        url=url,
                        max_s=price.max_s,
                        error=clone_links_constants().too_slow,
                    )
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
