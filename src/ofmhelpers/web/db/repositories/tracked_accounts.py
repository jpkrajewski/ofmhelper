"""Social accounts /accounts tracks, their scan history, and creators."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, or_, select, update
from sqlalchemy.dialects.postgresql import insert

from ofmhelpers.web.db.models import AccountSnapshotRow, CreatorRow, TrackedAccountRow
from ofmhelpers.web.db.repositories.cached_repository import (
    CachedRepository,
    cached,
    invalidates_cache,
)
from ofmhelpers.web.db.session import session_scope

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy import Row
    from sqlalchemy.orm import Session


def _to_dict(row: TrackedAccountRow, creator: str | None) -> dict:
    return {
        "id": row.id,
        "platform": row.platform,
        "handle": row.handle,
        "creator": creator,
        "notes": row.notes,
        "added_at": row.added_at,
        "scanned_at": row.scanned_at,
        "claimed_at": row.claimed_at,
        "error": row.error,
        "profile": row.profile,
        "metrics": row.metrics,
    }


def _creator_id(s: Session, name: str | None) -> int | None:
    """The creator called `name`, created on first use; None for none."""
    if not name:
        return None
    s.execute(
        insert(CreatorRow)
        .values(name=name, created_at=time.time())
        .on_conflict_do_nothing(index_elements=["name"])
    )
    return s.execute(select(CreatorRow.id).where(CreatorRow.name == name)).scalar_one()


def _baselines(
    s: Session, cutoff: float | None, account_id: int | None
) -> dict[int, tuple[int, float]]:
    """Per account (or only `account_id`), the newest snapshot taken at or
    before `cutoff` -- or, with no cutoff, the oldest one: (followers,
    taken_at)."""
    snap = AccountSnapshotRow
    query = select(snap.account_id, snap.followers, snap.taken_at).distinct(
        snap.account_id
    )
    if account_id is not None:
        query = query.where(snap.account_id == account_id)
    if cutoff is None:
        query = query.order_by(snap.account_id, snap.taken_at.asc())
    else:
        query = query.where(snap.taken_at <= cutoff).order_by(
            snap.account_id, snap.taken_at.desc()
        )
    return {a: (f, t) for a, f, t in s.execute(query).all()}


# Accounts with their creator's name.
_ACCOUNTS = select(TrackedAccountRow, CreatorRow.name).outerjoin(
    CreatorRow, TrackedAccountRow.creator_id == CreatorRow.id
)


def _with_baselines(
    s: Session, rows: Sequence[Row], periods_days: list[int], account_id: int | None
) -> list[dict]:
    """The (row, creator) results as dicts, each with `baselines`: per
    period, the newest snapshot at least that old (none for an account
    tracked for less), `first`: its first snapshot, for growth since it
    was added, and `latest`: its newest one (the last successful sync)."""
    now = time.time()
    first = _baselines(s, None, account_id)
    latest = _baselines(s, now, account_id)
    periods = {d: _baselines(s, now - d * 86400, account_id) for d in periods_days}
    out = []
    for row, creator in rows:
        item = _to_dict(row, creator)
        item["baselines"] = [
            {"days": d, "followers": base[0], "taken_at": base[1]}
            for d in periods_days
            if (base := periods[d].get(row.id))
        ]
        for key, found in (("first", first), ("latest", latest)):
            snap = found.get(row.id)
            item[key] = (
                {"days": 0, "followers": snap[0], "taken_at": snap[1]} if snap else None
            )
        out.append(item)
    return out


class TrackedAccountRepository(CachedRepository):
    cache_namespace = "tracked_account"

    @invalidates_cache
    def add(self, platform: str, handle: str, creator: str | None) -> bool:
        """True when the account is new. Adding one already tracked only
        assigns it to `creator`, when one is given."""
        with session_scope() as s:
            creator_id = _creator_id(s, creator)
            added = s.execute(
                insert(TrackedAccountRow)
                .values(
                    platform=platform,
                    handle=handle,
                    creator_id=creator_id,
                    notes="",
                    added_at=time.time(),
                )
                .on_conflict_do_nothing(
                    constraint="uq_tracked_accounts_platform_handle"
                )
                .returning(TrackedAccountRow.id)
            ).scalar_one_or_none()
            if added is None and creator_id is not None:
                s.execute(
                    update(TrackedAccountRow)
                    .where(
                        TrackedAccountRow.platform == platform,
                        TrackedAccountRow.handle == handle,
                    )
                    .values(creator_id=creator_id)
                )
            return added is not None

    @cached
    def list_all(self, periods_days: list[int]) -> list[dict]:
        """Every account (no posts), newest first, with its growth baselines
        (see `_with_baselines`)."""
        with session_scope() as s:
            rows = s.execute(
                _ACCOUNTS.order_by(
                    TrackedAccountRow.added_at.desc(), TrackedAccountRow.id.desc()
                )
            ).all()
            return _with_baselines(s, rows, periods_days, None)

    @cached
    def get(self, account_id: int, periods_days: list[int]) -> dict | None:
        """One account with its baselines and its latest scan's posts."""
        with session_scope() as s:
            rows = s.execute(_ACCOUNTS.where(TrackedAccountRow.id == account_id)).all()
            if not rows:
                return None
            item = _with_baselines(s, rows, periods_days, account_id)[0]
            return {**item, "posts": rows[0][0].posts or []}

    @cached
    def history(self, account_id: int, since: float) -> list[dict]:
        """The account's snapshots taken since `since`, oldest first."""
        snap = AccountSnapshotRow
        with session_scope() as s:
            rows = s.execute(
                select(snap.taken_at, snap.followers, snap.metrics)
                .where(snap.account_id == account_id, snap.taken_at >= since)
                .order_by(snap.taken_at)
            ).all()
            return [{"taken_at": t, "followers": f, "metrics": m} for t, f, m in rows]

    @cached
    def list_creators(self) -> list[str]:
        with session_scope() as s:
            return list(
                s.execute(select(CreatorRow.name).order_by(CreatorRow.name)).scalars()
            )

    @invalidates_cache
    def set_creator(self, account_ids: list[int], creator: str | None) -> int:
        """Assigns the accounts to `creator` (None: unassigned); how many
        exist."""
        with session_scope() as s:
            creator_id = _creator_id(s, creator)
            moved = s.execute(
                update(TrackedAccountRow)
                .where(TrackedAccountRow.id.in_(account_ids))
                .values(creator_id=creator_id)
                .returning(TrackedAccountRow.id)
            )
            return len(moved.all())

    @invalidates_cache
    def set_notes(self, account_id: int, notes: str) -> bool:
        with session_scope() as s:
            updated = s.execute(
                update(TrackedAccountRow)
                .where(TrackedAccountRow.id == account_id)
                .values(notes=notes)
                .returning(TrackedAccountRow.id)
            )
            return updated.scalar_one_or_none() is not None

    @invalidates_cache
    def delete(self, account_ids: list[int]) -> int:
        """Removes the accounts and their history; how many existed."""
        with session_scope() as s:
            removed = s.execute(
                delete(TrackedAccountRow)
                .where(TrackedAccountRow.id.in_(account_ids))
                .returning(TrackedAccountRow.id)
            )
            return len(removed.all())

    @invalidates_cache
    def mark_due(self, account_ids: list[int]) -> None:
        """Makes the accounts due now (and drops any claim), so the next
        sweep scans them."""
        with session_scope() as s:
            s.execute(
                update(TrackedAccountRow)
                .where(TrackedAccountRow.id.in_(account_ids))
                .values(scanned_at=None, claimed_at=None)
            )

    @invalidates_cache
    def claim_due(self, interval_s: float, claim_ttl_s: float) -> list[int]:
        """Marks every account not scanned for `interval_s` (and not claimed
        within `claim_ttl_s`) as claimed, in one UPDATE, and returns their
        ids: of two sweepers racing, each account goes to exactly one."""
        now = time.time()
        acc = TrackedAccountRow
        with session_scope() as s:
            claimed = s.execute(
                update(acc)
                .where(
                    or_(acc.scanned_at.is_(None), acc.scanned_at < now - interval_s),
                    or_(acc.claimed_at.is_(None), acc.claimed_at < now - claim_ttl_s),
                )
                .values(claimed_at=now)
                .returning(acc.id)
            )
            return sorted(claimed.scalars().all())

    @invalidates_cache
    def record_scan(
        self,
        account_id: int,
        profile: dict[str, Any] | None,
        metrics: dict[str, Any] | None,
        posts: list[Any] | None,
        error: str | None,
    ) -> None:
        """A failed scan (no `metrics`) keeps the previous numbers on show,
        next to its error. A successful one also appends a snapshot, in the
        same transaction."""
        now = time.time()
        values: dict[str, Any] = {"scanned_at": now, "claimed_at": None, "error": error}
        if metrics is not None:
            values |= {"profile": profile, "metrics": metrics, "posts": posts}
        with session_scope() as s:
            updated = s.execute(
                update(TrackedAccountRow)
                .where(TrackedAccountRow.id == account_id)
                .values(**values)
                .returning(TrackedAccountRow.id)
            ).scalar_one_or_none()
            if updated is not None and metrics is not None:
                s.add(
                    AccountSnapshotRow(
                        account_id=account_id,
                        taken_at=now,
                        followers=metrics["followers"],
                        metrics=metrics,
                    )
                )
