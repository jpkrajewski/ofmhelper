from collections import Counter
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from ofmhelpers.cache import enqueue
from ofmhelpers.config import settings
from ofmhelpers.db.cookies import store as login_cookies
from ofmhelpers.db.devices.catalog import services
from ofmhelpers.db.tracked_accounts.store import (
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
lazy from ofmhelpers.db.tracked_accounts.schema import TrackedAccount

logger = get_logger(__name__)

UNREACHED = "No source could read it now -- retried at the next scan"


class TrackedHandle(BaseModel, frozen=True):
    platform: str
    handle: str


def tracked_handle(service: str, username: str) -> TrackedHandle | None:
    found = services().get(service)
    if found is None or not found.tracked_platform:
        return None
    try:
        return TrackedHandle(
            platform=found.tracked_platform,
            handle=get_platform(found.tracked_platform).handle(username),
        )
    except KeyError, ValueError:
        logger.warning(
            "%s login %r is not a trackable handle", service, username, exc_info=True
        )
        return None


def track_login(service: str, username: str, *, user_id: int, revive: bool) -> bool:
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
    try:
        record_scan(account_id, stats, error)
    except Exception:
        logger.exception("saving the scan of account %d failed", account_id)


def scan_accounts(platform_key: str, account_ids: list[int], start: int = 0) -> None:
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


class _Batch(BaseModel, frozen=True):
    platform: str
    ids: list[int]


def _batches(due: list[int]) -> list[_Batch]:
    platform_of = {a.id: a.platform for a in list_accounts()}
    by_platform: dict[str, list[int]] = {}
    for account_id in due:
        if account_id in platform_of:
            by_platform.setdefault(platform_of[account_id], []).append(account_id)
    size = settings.accounts.batch_size
    return [
        _Batch(platform=platform, ids=ids[i : i + size])
        for platform, ids in by_platform.items()
        for i in range(0, len(ids), size)
    ]


def queue_due_scans() -> list[int]:
    s = settings.accounts
    due = claim_due(_rescan_interval_s(), s.claim_ttl_s, s.max_rescan_interval_s)
    if due:
        logger.info("rescanning %d tracked account(s)", len(due))
        for start, batch in enumerate(_batches(due)):
            enqueue(scan_accounts, (batch.platform, batch.ids, start))
    return due


def rescan_limited(user_id: int) -> list[int]:
    limited = [a.id for a in list_accounts(user_id) if a.metrics and a.metrics.limited]
    mark_due(limited)
    queue_due_scans()
    return limited


def sync_now(user_id: int) -> int:
    due = make_due(user_id)
    queue_due_scans()
    logger.info("sync: %d accounts of user %d queued", len(due), user_id)
    return len(due)


def _times_come(now: datetime) -> list[datetime]:
    zone = ZoneInfo(settings.accounts.scan_timezone)
    here = now.astimezone(zone)
    found = [
        datetime.combine(here.date() - timedelta(days=back), at, zone)
        for back in (0, 1)
        for at in settings.accounts.scan_time_list()
    ]
    return [at for at in found if at <= here]


def _rescan_interval_s() -> float:
    if not settings.accounts.scan_time_list():
        return settings.accounts.rescan_interval_s
    now = datetime.now(UTC)
    return (now - max(_times_come(now))).total_seconds()
