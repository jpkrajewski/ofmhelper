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
import random
import time

import requests

from ofmhelpers.cache import enqueue
from ofmhelpers.config import settings
from ofmhelpers.instagram import (
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
from ofmhelpers.web.stores.watched_profiles import (
    claim_due,
    get_watched,
    list_watched,
    record_scan,
    replace_result,
    unwatch,
)

logger = get_logger(__name__)

# Worth another try through another connection: a rate limit, a 5xx, a dead
# or flaky proxy (refused, TLS broken, dropped, timed out), and "not found"
# -- which a proxy IP Instagram has flagged gets for a real account too.
_RETRYABLE = (
    InstagramRateLimitedError,
    InstagramUnavailableError,
    ProfileNotFoundError,
    requests.RequestException,
)
# A proxy failing like this is dead for the rest of the job.
_DEAD_PROXY = (
    requests.exceptions.ProxyError,
    requests.exceptions.SSLError,
    requests.exceptions.ConnectTimeout,
)


class _OfflineError(Exception):
    """Even a direct connection fails: our own network is down (a DNS blip
    did exactly that on dev), so the rest of the batch would only fail the
    same way."""


class _ProxyPool:
    """One job's proxies, taken in turn, minus the ones found dead; direct
    once none is left (or none was configured)."""

    def __init__(self, urls: list[str]) -> None:
        self._live = list(urls)
        self._turn = 0

    def __len__(self) -> int:
        return len(self._live)

    def next(self) -> str | None:
        if not self._live:
            return None
        self._turn %= len(self._live)
        proxy = self._live[self._turn]
        self._turn += 1
        return proxy

    def drop(self, proxy: str | None) -> None:
        if proxy in self._live:
            # Shift the turn back past a dropped earlier entry, so the next
            # proxy in line is not skipped.
            if self._live.index(proxy) < self._turn:
                self._turn -= 1
            self._live.remove(proxy)
            # Never the URL itself: it carries the proxy's credentials.
            logger.warning("dead proxy skipped for this job; %d left", len(self))


def _is_new(username: str) -> bool:
    """Watched, but never scanned successfully: just added. A profile with
    a good scan behind it is never removed by a later one."""
    watched = get_watched(username)
    return watched is not None and watched.result is None


def _reject(username: str, reason: str) -> None:
    """A just-added profile that can never be scanned leaves the list: a
    watch list of dead and private accounts helps nobody. The page tells the
    user which ones were not added (static/js/reels.js)."""
    unwatch(username)
    logger.info("@%s not added: %s", username, reason)


def _confirmed_gone(username: str, answered: list[BaseException]) -> bool:
    """Every proxy said "no such profile", and the uploaded login -- which a
    flagged proxy IP cannot fool -- does not see it either (or there is no
    login to ask)."""
    if not all(isinstance(f, ProfileNotFoundError) for f in answered):
        return False
    return InstagramClient().exists_logged_in(username) is not True


def scan_watched(username: str, proxy: str | None = None) -> ProfileReels | None:
    """One scan (through `proxy`, when given), stored when it settles: its
    reels, or a lasting failure (private profile, rotated query) shown next
    to the previous result. Anything worth retrying elsewhere is raised and
    nothing is stored."""
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
    except _RETRYABLE:
        raise
    except ProfilePrivateError as exc:
        if _is_new(username):
            _reject(username, "private")
        else:
            record_scan(username, None, str(exc))
        return None
    except InstagramError as exc:
        record_scan(username, None, str(exc))
        return None
    record_scan(username, found)
    return found


def _failure(failures: list[BaseException]) -> str:
    """What the row shows once Instagram itself answered a failure on every
    try that reached it. A profile is never removed for it: the previous
    result stays, and the next rescan tries again."""
    tries = len(failures)
    if any(isinstance(f, ProfileNotFoundError) for f in failures):
        return (
            f"Instagram showed no such profile ({tries} tries) -- "
            "check the name, or remove it if the account is gone"
        )
    return f"Instagram answered errors ({tries} tries) -- retried at the next rescan"


def _scan_rotating(username: str, pool: _ProxyPool, tries: int) -> None:
    """One profile, up to `tries` times, each through the pool's next proxy
    after a jittered pause; a dead proxy leaves the pool. The batch stops
    only when every try was rate-limited, or a direct connection fails
    (_OfflineError). Tries that never reached Instagram record nothing --
    not the profile's fault; it stays claimed and is retried when the claim
    expires. What Instagram itself answered is recorded on the row and the
    batch goes on."""
    failures: list[BaseException] = []
    for attempt in range(tries):
        if attempt:
            time.sleep(random.uniform(0, settings.instagram.retry_jitter_s))  # noqa: S311 -- jitter, not crypto
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
    """The worker job: the claimed profiles, one at a time, through
    `settings.instagram.proxy_urls` in turn (direct without any), each tried
    on up to 1 + `proxy_retries` proxies. A profile still rate-limited after
    that, or the network being down, ends the job: the rest keep their
    claim, which expires after `rescan_claim_ttl_s`, and the next sweep
    picks them up then (with a fresh proxy pool)."""
    s = settings.instagram
    pool = _ProxyPool(s.proxy_urls)
    for done, username in enumerate(usernames):
        tries = 1 + min(s.proxy_retries, max(len(pool) - 1, 0))
        try:
            _scan_rotating(username, pool, tries)
        except InstagramRateLimitedError:
            logger.warning(
                "rate-limited after %d of %d profiles; the rest retry later",
                done,
                len(usernames),
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
    """A login-only reel with its mp4 and length, when the login sees it."""
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


def unlock_gated() -> None:
    """The worker job a cookies.txt upload queues: every stored login-only
    reel resolved through the new login now, not at its profile's next
    rescan. One unlocked past `max_duration_s` drops out, as a scan would
    have dropped it."""
    client = InstagramClient()
    max_s = settings.instagram.max_duration_s
    for watched in list_watched():
        result = watched.result
        if result is None or all(r.video_url for r in result.reels):
            continue
        try:
            reels = [_unlocked(client, r) for r in result.reels]
        except requests.RequestException:
            logger.warning("unlocking @%s failed", watched.username, exc_info=True)
            continue
        kept = [r for r in reels if r.duration_s is None or r.duration_s <= max_s]
        replace_result(watched.username, result.model_copy(update={"reels": kept}))


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
