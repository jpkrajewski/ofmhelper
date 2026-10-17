"""
ofmhelpers/web/reel_watch.py

Rescans every profile on /reels' watch list every `rescan_interval_s` (12 h by
default), so its viral reels stay current without anyone pressing Scan.

The sweeper only decides *who* is due: it claims them in one atomic UPDATE
(`stores.watched_profiles.claim_due`, so a second API process cannot claim
the same profile) and queues one worker job that scans them one after
another -- Instagram rate-limits logged-out bursts, so ten parallel workers
would only buy ten 429s. A claim that never completes (worker killed by a
deploy) expires after `rescan_claim_ttl_s` and the profile is picked up again.
"""

import asyncio
from collections.abc import Iterator
from itertools import cycle, repeat

import requests

from ofmhelpers.cache import enqueue
from ofmhelpers.config import settings
from ofmhelpers.instagram import (
    InstagramClient,
    InstagramError,
    InstagramRateLimitedError,
    ProfileNotFoundError,
    ProfileReels,
    scan_profile,
)
from ofmhelpers.log import get_logger
from ofmhelpers.web.stores.watched_profiles import claim_due, record_scan, unwatch

logger = get_logger(__name__)


def scan_watched(username: str, proxy: str | None = None) -> ProfileReels | None:
    """One scan (through `proxy`, when given), stored either way: its reels,
    or why it failed (the previous result stays on show next to the error).
    A rate limit or a dead proxy is raised instead and nothing is stored, so
    the caller can retry on another proxy and the profile stays due."""
    s = settings.instagram
    try:
        found = scan_profile(
            InstagramClient(proxy),
            username,
            s.min_views,
            s.max_duration_s,
            s.max_age_days * 86400,
            s.reels_per_profile,
        )
    except (InstagramRateLimitedError, requests.exceptions.ProxyError):
        raise
    except ProfileNotFoundError:
        # Deleted, banned or renamed: nothing to keep watching.
        logger.info("@%s does not exist; removed from the watch list", username)
        unwatch(username)
        return None
    except InstagramError as exc:
        record_scan(username, None, str(exc))
        return None
    except requests.RequestException:
        logger.warning("instagram scan of %s failed", username, exc_info=True)
        record_scan(username, None, "Instagram unreachable")
        return None
    record_scan(username, found)
    return found


def _scan_rotating(username: str, proxies: Iterator[str | None], tries: int) -> None:
    """One profile, moving to the next proxy when Instagram rate-limits the
    current one (or it is dead), `tries` times at most."""
    for attempt in range(tries):
        proxy = next(proxies)
        try:
            scan_watched(username, proxy)
        except (InstagramRateLimitedError, requests.exceptions.ProxyError):
            if attempt == tries - 1:
                raise
            logger.info("@%s: proxy %d refused, trying the next", username, attempt + 1)
        else:
            return


def scan_all(usernames: list[str]) -> None:
    """The worker job: the claimed profiles, one at a time, each through the
    next proxy of `settings.instagram.proxy_urls` (direct without any). A
    profile still rate-limited after `proxy_retries` proxies ends the job:
    the rest keep their claim, which expires after `rescan_claim_ttl_s`, and
    the next sweep picks them up then."""
    s = settings.instagram
    urls = s.proxy_urls
    proxies: Iterator[str | None] = cycle(urls) if urls else repeat(None)
    tries = 1 + min(s.proxy_retries, len(urls) - 1) if urls else 1
    for done, username in enumerate(usernames):
        try:
            _scan_rotating(username, proxies, tries)
        except (InstagramRateLimitedError, requests.exceptions.ProxyError):
            logger.warning(
                "rate-limited after %d of %d profiles; the rest retry later",
                done,
                len(usernames),
            )
            return


def queue_due_scans() -> list[str]:
    s = settings.instagram
    due = claim_due(s.rescan_interval_s, s.rescan_claim_ttl_s)
    if due:
        logger.info("rescanning %d watched profile(s)", len(due))
        enqueue(scan_all, due)
    return due


async def reel_watch_loop() -> None:
    while True:
        try:
            await asyncio.to_thread(queue_due_scans)
        except Exception:
            # A broken sweep must never take the loop down -- log and move on.
            logger.exception("reel watch sweep failed")
        await asyncio.sleep(settings.instagram.rescan_check_interval_s)
