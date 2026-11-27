# Copyright (C) 2026 jpkrajewski

import time
from functools import lru_cache

from ofmhelpers.config import settings
from ofmhelpers.db.tracked_accounts.models import platform_names
from ofmhelpers.db.tracked_accounts.repository import TrackedAccountRepository
from ofmhelpers.db.tracked_accounts.schema import AccountHistory, TrackedAccount
from ofmhelpers.db.users.store import agency_id
lazy from ofmhelpers.db.tracked_accounts.schema import AccountStats, Creator


@lru_cache(maxsize=1)
def _repository() -> TrackedAccountRepository:
    return TrackedAccountRepository()


def _account(row: TrackedAccount) -> TrackedAccount:
    claimed = row.claimed_at
    since = time.time() - settings.accounts.claim_ttl_s
    return row.model_copy(update={"syncing": claimed is not None and claimed > since})


def list_accounts(owner: int | None = None) -> list[TrackedAccount]:
    rows = _repository().list_all(settings.accounts.periods_days, owner)
    return [_account(row) for row in rows if row.platform in platform_names()]


def get_account(account_id: int, owner: int | None = None) -> TrackedAccount | None:
    row = _repository().get(account_id, settings.accounts.periods_days)
    if row is None or row.platform not in platform_names():
        return None
    if owner is not None and row.user_id != owner:
        return None
    return _account(row)


def get_history(account_id: int) -> AccountHistory:
    since = time.time() - settings.accounts.history_days * 86400
    rows = _repository().history(account_id, since - since % 3600)
    return AccountHistory(snapshots=rows)


def list_creators(owner: int | None = None) -> list[str]:
    return _repository().list_creators(owner)


def creators_of(owner: int) -> list[Creator]:
    return _repository().creators(owner)


def track(
    platform: str,
    handle: str,
    creator: str | None = None,
    *,
    user_id: int | None = None,
    revive: bool = True,
) -> bool:
    owner = agency_id() if user_id is None else user_id
    return _repository().add(platform, handle, creator, owner, revive)


def assign_creator(
    account_ids: list[int],
    creator: str | None,
    owner: int | None = None,
) -> int:
    return _repository().set_creator(account_ids, creator, owner)


def set_notes(account_id: int, notes: str, owner: int | None = None) -> bool:
    return _repository().set_notes(account_id, notes, owner)


def untrack(account_ids: list[int], owner: int | None = None) -> int:
    return _repository().untrack(account_ids, owner)


def mark_due(account_ids: list[int]) -> None:
    if account_ids:
        _repository().mark_due(account_ids)


def make_due(owner: int) -> list[int]:
    return _repository().make_due(owner, settings.accounts.claim_ttl_s)


def claim_due(
    interval_s: float,
    claim_ttl_s: float,
    max_interval_s: float | None = None,
) -> list[int]:
    ceiling = interval_s if max_interval_s is None else max_interval_s
    return _repository().claim_due(interval_s, claim_ttl_s, ceiling)


def record_scan(
    account_id: int,
    stats: AccountStats | None,
    error: str | None = None,
) -> None:
    retention = settings.accounts.post_retention_days
    if stats is None:
        _repository().record_scan(account_id, None, None, [], None, retention, error)
        return
    _repository().record_scan(
        account_id,
        stats.profile.model_dump(mode="json"),
        stats.metrics().model_dump(mode="json"),
        [p.model_dump(mode="json") for p in stats.posts],
        stats.reach,
        retention,
        None,
    )
