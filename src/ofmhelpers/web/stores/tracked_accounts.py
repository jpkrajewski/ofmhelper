"""
ofmhelpers/web/stores/tracked_accounts.py

The social accounts /accounts tracks (routers/tracking/accounts.py adds,
assigns and removes them; web/account_tracker.py rescans them), each with
its latest numbers, growth and history.
"""

from __future__ import annotations

import time
from functools import lru_cache
from typing import TYPE_CHECKING

from ofmhelpers.config import settings
from ofmhelpers.socials import platforms
from ofmhelpers.web.db.repositories import TrackedAccountRepository
from ofmhelpers.web.schemas import AccountHistory, TrackedAccount

if TYPE_CHECKING:
    from ofmhelpers.socials import AccountStats


@lru_cache(maxsize=1)
def _repository() -> TrackedAccountRepository:
    """Built on first use rather than at import (constructing it binds a Redis
    connection for its cache)."""
    return TrackedAccountRepository()


def list_accounts() -> list[TrackedAccount]:
    """Every tracked account of a registered platform, newest first."""
    rows = _repository().list_all(settings.accounts.periods_days)
    return [
        TrackedAccount.model_validate(row)
        for row in rows
        if row["platform"] in platforms()
    ]


def get_account(account_id: int) -> TrackedAccount | None:
    row = _repository().get(account_id, settings.accounts.periods_days)
    if row is None or row["platform"] not in platforms():
        return None
    return TrackedAccount.model_validate(row)


def get_history(account_id: int) -> AccountHistory:
    """The last `history_days` of snapshots. The cutoff is rounded to the
    hour, so the read stays cacheable."""
    since = time.time() - settings.accounts.history_days * 86400
    rows = _repository().history(account_id, since - since % 3600)
    return AccountHistory.model_validate({"snapshots": rows})


def list_creators() -> list[str]:
    return _repository().list_creators()


def track(platform: str, handle: str, creator: str | None = None) -> bool:
    """True when the account is new or tracked again (an untracked one comes
    back with its history); one already tracked is only assigned to
    `creator` (when given)."""
    return _repository().add(platform, handle, creator)


def assign_creator(account_ids: list[int], creator: str | None) -> int:
    return _repository().set_creator(account_ids, creator)


def set_notes(account_id: int, notes: str) -> bool:
    return _repository().set_notes(account_id, notes)


def untrack(account_ids: list[int]) -> int:
    """Stops tracking the accounts; nothing of theirs is deleted."""
    return _repository().untrack(account_ids)


def mark_due(account_ids: list[int]) -> None:
    if account_ids:
        _repository().mark_due(account_ids)


def claim_due(
    interval_s: float, claim_ttl_s: float, max_interval_s: float | None = None
) -> list[int]:
    """The due accounts, claimed (see the repository); `max_interval_s`
    None: no backoff for failing ones."""
    ceiling = interval_s if max_interval_s is None else max_interval_s
    return _repository().claim_due(interval_s, claim_ttl_s, ceiling)


def record_scan(
    account_id: int, stats: AccountStats | None, error: str | None = None
) -> None:
    """A scan's result (`stats`) or lasting failure (`error`)."""
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
