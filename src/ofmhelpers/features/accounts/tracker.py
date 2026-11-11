"""
ofmhelpers/web/account_tracker.py

Rescans every tracked social account every `rescan_interval_s` (6 h by
default), so /accounts' followers, views and growth stay current without
anyone pressing a button. The periodic sweep runs at fixed times instead: `OFM_ACCOUNTS_SCAN_TIMES`
(13:50 Warsaw, 10 min before the daily Telegram report, and 18:00) on
`OFM_ACCOUNTS_SCAN_TIMEZONE`'s clock (`_rescan_interval_s`).
Platform-agnostic: a scan goes through the platform's sources in turn
(`socials.Platform.sources`, `socials.scan_through`: Apify, our client
through the proxies, our client logged in), each taking only what the ones
before it left unread.

The sweeper claims due accounts in one atomic UPDATE (a second API process
cannot claim the same ones) and queues them per platform in jobs of
`batch_size`, each starting on its own proxy, so parallel jobs spread over
the IPs. A claim that never completes (worker killed by a deploy, every
source down) expires after `claim_ttl_s` and the account is picked up again.
/accounts Sync (`sync_now`) makes all of a user's accounts due at once.

Only the last source logs in, with the owner's own cookies.txt
(`stores.cookies.for_user`), never another user's.
"""

from collections import Counter
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from ofmhelpers.cache import enqueue
from ofmhelpers.config import settings
from ofmhelpers.db.stores import cookies as login_cookies
from ofmhelpers.db.stores.tracked_accounts import (
    claim_due,
    get_account,
    list_accounts,
    make_due,
    mark_due,
    record_scan,
    track,
)
from ofmhelpers.integrations.socials import (
    AccountStats,
    ScanTarget,
    get_platform,
    scan_through,
)
from ofmhelpers.log import get_logger
from ofmhelpers.schemas.phone_catalog import services
lazy from ofmhelpers.schemas import TrackedAccount

logger = get_logger(__name__)

# An account no source could reach this time (keys out of credit, proxies
# rate-limited, no login): the row says so, its numbers stay.
UNREACHED = "No source could read it now -- retried at the next scan"


class TrackedHandle(BaseModel, frozen=True):
    """What a phone login is tracked as on /accounts."""

    platform: str
    handle: str


def tracked_handle(service: str, username: str) -> TrackedHandle | None:
    """What a phone login of `service` is tracked as on
    /accounts -- any service whose services.json entry names a
    `tracked_platform` -- or None (not a tracked service, or a username that
    platform does not take)."""
    found = services().get(service)
    if found is None or not found.tracked_platform:
        return None
    try:
        return TrackedHandle(
            platform=found.tracked_platform,
            handle=get_platform(found.tracked_platform).handle(username),
        )
    except KeyError, ValueError:
        logger.warning("%s login %r is not a trackable handle", service, username)
        return None


def track_login(service: str, username: str, *, user_id: int, revive: bool) -> bool:
    """A login added to a phone shows on /accounts (with that phone): its
    account is tracked for the phone's owner (`user_id`) and its first scan
    queued. `revive`: also bring back
    one removed by hand (an add does; an edit does not). True when newly
    tracked. Never raises: the login is saved already, and tracking it is a
    side effect that must not fail that save -- a failure is logged."""
    try:
        key = tracked_handle(service, username)
        if key is None or not track(
            key.platform, key.handle, user_id=user_id, revive=revive
        ):
            return False
        queue_due_scans()
    except Exception:
        logger.exception("tracking %s login %r failed", service, username)
        return False
    return True


def _targets(accounts: list[TrackedAccount]) -> list[ScanTarget]:
    """The accounts as the sources read them, each with its owner's own
    cookies (read once per owner; only the login source uses them)."""
    owners: dict[int, str | None] = {}
    targets = []
    for account in accounts:
        if account.user_id not in owners:
            owners[account.user_id] = login_cookies.for_user(account.user_id)
        targets.append(
            ScanTarget(
                key=str(account.id),
                handle=account.handle,
                cookies=owners[account.user_id],
            )
        )
    return targets


def _save(
    account_id: int, stats: AccountStats | None, error: str | None = None
) -> None:
    """One account's outcome; a failure to save it is logged and never costs
    the rest of the batch theirs."""
    try:
        record_scan(account_id, stats, error)
    except Exception:
        logger.exception("saving the scan of account %d failed", account_id)


def scan_accounts(platform_key: str, account_ids: list[int], start: int = 0) -> None:
    """The worker job: one platform's claimed accounts through its sources
    in turn, the proxied ones starting at proxy `start`. An account removed
    since it was claimed (or untracked) is skipped. Read: its numbers saved
    the moment its source answered (a job killed later never pays for that
    read again). Unread: the platform's own answer (not found, refused on
    every try), else `UNREACHED`, saved as its error -- numbers kept, the
    claim released, retried at the next scan with the failure backoff (a
    claim left standing would be re-tried, and Apify paid, every
    `claim_ttl_s`, and Sync stay greyed out that long)."""
    platform = get_platform(platform_key)
    accounts = [
        a
        for account_id in account_ids
        if (a := get_account(account_id)) is not None and a.untracked_at is None
    ]
    answers = scan_through(
        platform.sources(start),
        _targets(accounts),
        lambda key, stats: _save(int(key), stats),
    )
    for account in accounts:
        if str(account.id) in answers.stats:
            continue
        refusal = answers.refusals.get(str(account.id))
        message = refusal.message if refusal and refusal.reached else UNREACHED
        logger.warning("%s @%s: not read: %s", platform_key, account.handle, message)
        _save(account.id, None, message)
    logger.info(
        "%s: %d of %d accounts read, by source %s",
        platform_key,
        len(answers.stats),
        len(accounts),
        dict(Counter(s.source for s in answers.stats.values())),
    )


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
    due = claim_due(_rescan_interval_s(), s.claim_ttl_s, s.max_rescan_interval_s)
    if due:
        logger.info("rescanning %d tracked account(s)", len(due))
        for start, (platform, ids) in enumerate(_batches(due)):
            enqueue(scan_accounts, platform, ids, start)
    return due


def rescan_limited(user_id: int) -> list[int]:
    """Queues every account of `user_id`'s last synced from a restricted
    (logged-out) view -- what their fresh login upload is for: their
    numbers now, not at the next rescan. Nobody else's: those scans would
    not use this login."""
    limited = [a.id for a in list_accounts(user_id) if a.metrics and a.metrics.limited]
    mark_due(limited)
    queue_due_scans()
    return limited


def sync_now(user_id: int) -> int:
    """/accounts Sync: every account of `user_id` scanned now, not at the
    next scan time. One already queued or running is left to finish. How
    many were queued."""
    due = make_due(user_id)
    queue_due_scans()
    logger.info("sync: %d accounts of user %d queued", len(due), user_id)
    return len(due)


def _times_come(now: datetime) -> list[datetime]:
    """Today's and yesterday's scan times (`OFM_ACCOUNTS_SCAN_TIMES` on
    `OFM_ACCOUNTS_SCAN_TIMEZONE`'s clock) that have come by `now`."""
    zone = ZoneInfo(settings.accounts.scan_timezone)
    here = now.astimezone(zone)
    found = [
        datetime.combine(here.date() - timedelta(days=back), at, zone)
        for back in (0, 1)
        for at in settings.accounts.scan_time_list()
    ]
    return [at for at in found if at <= here]


def _rescan_interval_s() -> float:
    """How old a scan may get. With scan times: not older than the last one that
    came, so the runs stay at those times, and one missed (the app down at 18:00)
    runs on the next pass. "24 h since the last scan" slides later every day and
    never comes back (prod: window 05:00, scans at 21:00, 2026-10-29..11-02). First
    scans (a login just added) and a fresh cookies upload queue directly."""
    if not settings.accounts.scan_time_list():
        return settings.accounts.rescan_interval_s
    now = datetime.now(UTC)
    return (now - max(_times_come(now))).total_seconds()
