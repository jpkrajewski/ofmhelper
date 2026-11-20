import random
import time

import requests

from ofmhelpers.cache import enqueue
from ofmhelpers.config import settings
from ofmhelpers.db.stores import cookies as login_cookies
from ofmhelpers.db.stores.watched_profiles import (
    claim_due,
    get_watched,
    list_watched,
    record_scan,
    replace_result,
    unwatch_unscanned,
)
from ofmhelpers.integrations.instagram import (
    InstagramClient,
    InstagramError,
    InstagramRateLimitedError,
    InstagramUnavailableError,
    ProfileNotFoundError,
    ProfilePrivateError,
    ProfileReels,
    Reel,
    scan_profile,
)
from ofmhelpers.log import get_logger
from ofmhelpers.utils.proxy_pool import ProxyPool

logger = get_logger(__name__)

_RETRYABLE = (
    InstagramRateLimitedError,
    InstagramUnavailableError,
    ProfileNotFoundError,
    requests.RequestException,
)
_DEAD_PROXY = (
    requests.exceptions.ProxyError,
    requests.exceptions.SSLError,
    requests.exceptions.ConnectTimeout,
)


class _OfflineError(Exception): ...


def _is_new(username: str) -> bool:
    watched = get_watched(username)
    return watched is not None and watched.result is None


def _reject(username: str, reason: str) -> None:
    unwatch_unscanned(username)
    logger.info("@%s not added: %s", username, reason)


def _confirmed_gone(username: str, answered: list[BaseException]) -> bool:
    if not all(isinstance(f, ProfileNotFoundError) for f in answered):
        return False
    client = InstagramClient(cookies=login_cookies.for_watched(username))
    return client.exists_logged_in(username) is not True


def scan_watched(username: str, proxy: str | None = None) -> ProfileReels | None:
    s = settings.instagram
    try:
        found = scan_profile(
            InstagramClient(proxy, login_cookies.for_watched(username)),
            username,
            s.min_views,
            s.max_duration_s,
            s.max_age_days * 86400,
            s.reels_per_profile,
        )
    except _RETRYABLE:
        raise
    except ProfilePrivateError as exc:
        if _is_new(username):
            _reject(username, "private")
        else:
            logger.warning("@%s not scanned: %s", username, exc, exc_info=True)
            record_scan(username, None, str(exc))
        return None
    except InstagramError as exc:
        logger.warning("@%s not scanned: %s", username, exc, exc_info=True)
        record_scan(username, None, str(exc))
        return None
    record_scan(username, found)
    return found


def _failure(failures: list[BaseException]) -> str:
    tries = len(failures)
    if any(isinstance(f, ProfileNotFoundError) for f in failures):
        return (
            f"Instagram showed no such profile ({tries} tries) -- "
            "check the name, or remove it if the account is gone"
        )
    return f"Instagram answered errors ({tries} tries) -- retried at the next rescan"


def _scan_rotating(username: str, pool: ProxyPool, tries: int) -> None:
    failures: list[BaseException] = []
    for attempt in range(tries):
        if attempt:
            time.sleep(random.uniform(0, settings.instagram.retry_jitter_s))  # noqa: S311
        proxy = pool.next()
        try:
            scan_watched(username, proxy)
        except _RETRYABLE as exc:
            failures.append(exc)
            if isinstance(exc, requests.ConnectionError) and proxy is None:
                raise _OfflineError from exc
            if isinstance(exc, _DEAD_PROXY):
                pool.drop(proxy)
            logger.info(
                "@%s: try %d failed (%s)", username, attempt + 1, type(exc).__name__
            )
        else:
            return
    if all(isinstance(f, InstagramRateLimitedError) for f in failures):
        raise failures[-1]
    answered = [f for f in failures if not isinstance(f, requests.RequestException)]
    if not answered:
        logger.warning("@%s: no try reached Instagram; retried later", username)
        return
    if _is_new(username) and _confirmed_gone(username, answered):
        _reject(username, "account deleted or renamed")
        return
    record_scan(username, None, _failure(answered))


def scan_all(usernames: list[str]) -> None:
    s = settings.instagram
    pool = ProxyPool(s.proxy_urls)
    for done, username in enumerate(usernames):
        tries = 1 + min(s.proxy_retries, max(len(pool) - 1, 0))
        try:
            _scan_rotating(username, pool, tries)
        except InstagramRateLimitedError:
            logger.warning(
                "rate-limited after %d of %d profiles; the rest retry later",
                done,
                len(usernames),
                exc_info=True,
            )
            return
        except _OfflineError:
            logger.warning(
                "network down after %d of %d profiles; the rest retry later",
                done,
                len(usernames),
                exc_info=True,
            )
            return


def _unlocked(client: InstagramClient, reel: Reel) -> Reel:
    if reel.video_url:
        return reel
    media = client.media_logged_in(reel.pk)
    if media is None:
        return reel
    return reel.model_copy(
        update={
            "video_url": media.video_url,
            "duration_s": media.duration_s,
            "taken_at": media.taken_at,
        }
    )


def unlock_gated(user_id: int) -> None:
    max_s = settings.instagram.max_duration_s
    for watched in list_watched(owner=user_id):
        result = watched.result
        if result is None or all(r.video_url for r in result.reels):
            continue
        client = InstagramClient(cookies=login_cookies.for_watched(watched.username))
        try:
            reels = [_unlocked(client, r) for r in result.reels]
        except requests.RequestException:
            logger.warning("unlocking @%s failed", watched.username, exc_info=True)
            continue
        kept = [r for r in reels if r.duration_s is None or r.duration_s <= max_s]
        replace_result(watched.username, result.model_copy(update={"reels": kept}))


def queue_due_scans() -> list[str]:
    s = settings.instagram
    interval = s.rescan_interval_s if s.rescan_enabled else None
    due = claim_due(interval, s.rescan_claim_ttl_s)
    if due:
        logger.info("rescanning %d watched profile(s)", len(due))
        enqueue(scan_all, due)
    return due
