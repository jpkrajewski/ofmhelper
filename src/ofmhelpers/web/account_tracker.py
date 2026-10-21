"""
ofmhelpers/web/account_tracker.py

Rescans every tracked social account every `rescan_interval_s` (6 h by
default), so /accounts' followers, views and growth stay current without
anyone pressing a button. Platform-agnostic: everything platform-specific is
behind `socials.Platform.fetch`.

The sweeper claims due accounts in one atomic UPDATE (a second API process
cannot claim the same ones) and queues them per platform in jobs of
`batch_size`; each job scans its accounts one after another, rotating
through the platform's proxies from its own starting point, so parallel
jobs spread over the IPs. A claim that never completes (worker killed by a
deploy, batch stopped by a rate limit) expires after `claim_ttl_s` and the
account is picked up again.
"""

import asyncio
import random
import time

from ofmhelpers.cache import enqueue
from ofmhelpers.config import settings
from ofmhelpers.log import get_logger
from ofmhelpers.phones import services
from ofmhelpers.socials import (
    Platform,
    RateLimitedError,
    RetryableError,
    SocialError,
    get_platform,
)
from ofmhelpers.utils.proxy_pool import ProxyPool
from ofmhelpers.web.schemas import TrackedAccount
from ofmhelpers.web.stores.tracked_accounts import (
    claim_due,
    get_account,
    list_accounts,
    mark_due,
    record_scan,
    track,
)

logger = get_logger(__name__)


class _OfflineError(Exception):
    """Even a direct connection fails: our own network is down, so the rest
    of the batch would only fail the same way."""


def _failure(answered: list[RetryableError], tries: int) -> str:
    """What the row shows once the platform itself answered a failure on
    every try that reached it. The previous numbers stay."""
    return f"{answered[-1]} ({tries} tries) -- retried at the next rescan"


def tracked_handle(service: str, username: str) -> tuple[str, str] | None:
    """(platform, handle) a phone login of `service` is tracked as on
    /accounts -- any service whose services.json entry names a
    `tracked_platform` -- or None (not a tracked service, or a username that
    platform does not take)."""
    found = services().get(service)
    if found is None or not found.tracked_platform:
        return None
    try:
        return found.tracked_platform, get_platform(found.tracked_platform).handle(
            username
        )
    except (KeyError, ValueError):
        logger.warning("%s login %r is not a trackable handle", service, username)
        return None


def track_login(service: str, username: str, *, revive: bool) -> bool:
    """A login added to a phone shows on /accounts (with that phone): its
    account is tracked and its first scan queued. `revive`: also bring back
    one removed by hand (an add does; an edit does not). True when newly
    tracked. Never raises: the login is saved already, and tracking it is a
    side effect that must not fail that save -- a failure is logged."""
    try:
        key = tracked_handle(service, username)
        if key is None or not track(*key, revive=revive):
            return False
        queue_due_scans()
    except Exception:
        logger.exception("tracking %s login %r failed", service, username)
        return False
    return True


def scan_account(
    account: TrackedAccount, platform: Platform, pool: ProxyPool, tries: int
) -> None:
    """One account, up to `tries` times, each through the pool's next proxy
    after a jittered pause; a dead proxy leaves the pool. Raises
    RateLimitedError when every try was rate-limited and _OfflineError when
    a direct connection fails -- the batch stops on both. Tries that never
    reached the platform record nothing (not the account's fault): it stays
    claimed and is retried when the claim expires."""
    failures: list[RetryableError] = []
    for attempt in range(tries):
        if attempt:
            time.sleep(random.uniform(0, settings.accounts.retry_jitter_s))  # noqa: S311 -- jitter, not crypto
        proxy = pool.next()
        try:
            stats = platform.fetch(account.handle, proxy)
        except RetryableError as exc:
            failures.append(exc)
            if not exc.reached and proxy is None:
                raise _OfflineError from exc
            if exc.dead_proxy:
                pool.drop(proxy)
            logger.info(
                "%s @%s: try %d failed (%s)",
                platform.key,
                account.handle,
                attempt + 1,
                exc,
            )
            continue
        except SocialError as exc:
            record_scan(account.id, None, str(exc))
            return
        record_scan(account.id, stats)
        return
    if all(isinstance(f, RateLimitedError) for f in failures):
        raise failures[-1]
    answered = [f for f in failures if f.reached]
    if not answered:
        logger.warning("%s @%s: no try got through", platform.key, account.handle)
        return
    record_scan(account.id, None, _failure(answered, len(failures)))


def scan_accounts(platform_key: str, account_ids: list[int], start: int = 0) -> None:
    """The worker job: one platform's claimed accounts, one at a time, each
    tried on up to 1 + `proxy_retries` of the platform's proxies (direct
    without any), starting at proxy `start`. An account removed since it
    was claimed (or untracked) is skipped."""
    platform = get_platform(platform_key)
    pool = ProxyPool(platform.proxies(), start)
    retries = settings.accounts.proxy_retries
    for done, account_id in enumerate(account_ids):
        account = get_account(account_id)
        if account is None or account.untracked_at is not None:
            continue
        tries = 1 + min(retries, max(len(pool) - 1, 0))
        try:
            scan_account(account, platform, pool, tries)
        except RateLimitedError:
            logger.warning(
                "%s rate-limited after %d of %d accounts; the rest retry later",
                platform_key,
                done,
                len(account_ids),
            )
            return
        except _OfflineError:
            logger.warning(
                "network down after %d of %d accounts; the rest retry later",
                done,
                len(account_ids),
                exc_info=True,
            )
            return


def _batches(due: list[int]) -> list[tuple[str, list[int]]]:
    """The claimed ids as (platform, ids) jobs of at most `batch_size`."""
    platform_of = {a.id: a.platform for a in list_accounts()}
    by_platform: dict[str, list[int]] = {}
    for account_id in due:
        if account_id in platform_of:
            by_platform.setdefault(platform_of[account_id], []).append(account_id)
    size = settings.accounts.batch_size
    return [
        (platform, ids[i : i + size])
        for platform, ids in by_platform.items()
        for i in range(0, len(ids), size)
    ]


def queue_due_scans() -> list[int]:
    s = settings.accounts
    due = claim_due(s.rescan_interval_s, s.claim_ttl_s, s.max_rescan_interval_s)
    if due:
        logger.info("rescanning %d tracked account(s)", len(due))
        for start, (platform, ids) in enumerate(_batches(due)):
            enqueue(scan_accounts, platform, ids, start)
    return due


def rescan_limited() -> list[int]:
    """Queues every account last synced from a restricted (logged-out) view
    -- what a fresh login upload is for: their numbers now, not at the next
    rescan."""
    limited = [a.id for a in list_accounts() if a.metrics and a.metrics.limited]
    mark_due(limited)
    queue_due_scans()
    return limited


async def account_tracker_loop() -> None:
    while True:
        try:
            await asyncio.to_thread(queue_due_scans)
        except Exception:
            # A broken sweep must never take the loop down -- log and move on.
            logger.exception("account tracker sweep failed")
        await asyncio.sleep(settings.accounts.rescan_check_interval_s)
