"""Instagram profiles /reels rescans, and each one's latest scan.

A row per owner and username: each user has their own watch list and
groups (`owner` limits a read to theirs; None: everyone's, for the
sweeper; a write on a row always names its user, never all of them).
A username is scanned once whoever watches it -- the scan-side methods
(`claim_due`, `record_scan`, `replace_result`) act on every row of it --
and a user adding a profile someone already watches gets its scan at
once."""

from __future__ import annotations

import time
from typing import Any

from sqlalchemy import ColumnElement, delete, or_, select, update
from sqlalchemy.dialects.postgresql import insert

from ofmhelpers.web.db.models import (
    ProfileGroupRow,
    ProfileScanStatRow,
    WatchedProfileRow,
)
from ofmhelpers.web.db.repositories.cached_repository import (
    CachedRepository,
    cached,
    invalidates_cache,
)
from ofmhelpers.web.db.repositories.owner_scope import mine
from ofmhelpers.web.db.session import session_scope


def _to_dict(row: WatchedProfileRow) -> dict:
    return {
        "user_id": row.user_id,
        "username": row.username,
        "group": row.group_name,
        "added_at": row.added_at,
        "scanned_at": row.scanned_at,
        "error": row.error,
        "result": row.result,
    }


class WatchedProfileRepository(CachedRepository):
    cache_namespace = "watched_profile"

    @invalidates_cache
    def add(self, username: str, group: str | None, user_id: int) -> bool:
        """True when the profile is new to `user_id`'s list. Adding one
        already on it only moves it to `group`, when a group is given. A
        profile someone else watches comes with its latest scan."""
        row = WatchedProfileRow
        with session_scope() as s:
            existing = s.get(row, (user_id, username))
            if existing is not None:
                if group is not None:
                    existing.group_name = group
                return False
            other = s.execute(
                select(row).where(row.username == username).limit(1)
            ).scalar_one_or_none()
            # Only a scan there is: a None would store JSON null, not NULL.
            scan = (
                {
                    "scanned_at": other.scanned_at,
                    "error": other.error,
                    "result": other.result,
                }
                if other is not None and other.result is not None
                else {}
            )
            added = s.execute(
                insert(row)
                .values(
                    user_id=user_id,
                    username=username,
                    group_name=group,
                    added_at=time.time(),
                    **scan,
                )
                .on_conflict_do_nothing(index_elements=["user_id", "username"])
                .returning(row.username)
            )
            return added.scalar_one_or_none() is not None

    @cached
    def list_all(self, owner: int | None = None) -> list[dict]:
        """`owner`'s watch list, oldest first, so a newly added profile
        lands at the bottom."""
        with session_scope() as s:
            rows = (
                s.execute(
                    select(WatchedProfileRow)
                    .where(*mine(WatchedProfileRow.user_id, owner))
                    .order_by(WatchedProfileRow.added_at, WatchedProfileRow.user_id)
                )
                .scalars()
                .all()
            )
            return [_to_dict(r) for r in rows]

    @invalidates_cache
    def create_group(self, name: str, user_id: int) -> None:
        """Creating a group the user has already is a no-op."""
        with session_scope() as s:
            s.execute(
                insert(ProfileGroupRow)
                .values(user_id=user_id, name=name, created_at=time.time())
                .on_conflict_do_nothing(index_elements=["user_id", "name"])
            )

    @cached
    def list_groups(self, owner: int | None = None) -> list[str]:
        """`owner`'s groups: created ones, empty or not, and any of their
        profiles is in."""
        with session_scope() as s:
            created = (
                s.execute(
                    select(ProfileGroupRow.name).where(
                        *mine(ProfileGroupRow.user_id, owner)
                    )
                )
                .scalars()
                .all()
            )
            used = (
                s.execute(
                    select(WatchedProfileRow.group_name)
                    .where(
                        WatchedProfileRow.group_name.is_not(None),
                        *mine(WatchedProfileRow.user_id, owner),
                    )
                    .distinct()
                )
                .scalars()
                .all()
            )
            return sorted({*created, *(g for g in used if g)})

    @invalidates_cache
    def set_group(self, username: str, group: str | None, user_id: int) -> bool:
        """Moves `user_id`'s row only: another user's same profile keeps
        its group, whoever (an admin too) moves this one."""
        with session_scope() as s:
            moved = s.execute(
                update(WatchedProfileRow)
                .where(
                    WatchedProfileRow.username == username,
                    WatchedProfileRow.user_id == user_id,
                )
                .values(group_name=group)
                .returning(WatchedProfileRow.username)
            )
            return bool(moved.all())

    @invalidates_cache
    def delete(self, username: str, user_id: int) -> bool:
        """Off `user_id`'s list only: an admin removing a profile leaves
        everyone else watching it."""
        row = WatchedProfileRow
        with session_scope() as s:
            removed = s.execute(
                delete(row)
                .where(row.username == username, row.user_id == user_id)
                .returning(row.username)
            )
            return bool(removed.all())

    @invalidates_cache
    def delete_unscanned(self, username: str) -> bool:
        """Off every list that no scan of it ever filled (a just-added
        profile that can't be read)."""
        row = WatchedProfileRow
        with session_scope() as s:
            removed = s.execute(
                delete(row)
                .where(row.username == username, row.result.is_(None))
                .returning(row.username)
            )
            return bool(removed.all())

    @invalidates_cache
    def claim_due(self, interval_s: float | None, claim_ttl_s: float) -> list[str]:
        """Marks every profile never scanned or not scanned for `interval_s`
        (None: never scanned only), and not already claimed within
        `claim_ttl_s`, as claimed, in one UPDATE, and returns them: of two
        sweepers racing, each profile goes to exactly one."""
        now = time.time()
        due: ColumnElement[bool] = WatchedProfileRow.scanned_at.is_(None)
        if interval_s is not None:
            due = or_(due, WatchedProfileRow.scanned_at < now - interval_s)
        with session_scope() as s:
            claimed = s.execute(
                update(WatchedProfileRow)
                .where(
                    due,
                    or_(
                        WatchedProfileRow.claimed_at.is_(None),
                        WatchedProfileRow.claimed_at < now - claim_ttl_s,
                    ),
                )
                .values(claimed_at=now)
                .returning(WatchedProfileRow.username)
            )
            return sorted(set(claimed.scalars().all()))

    @invalidates_cache
    def replace_result(self, username: str, result: dict[str, Any]) -> None:
        """The stored scan rewritten in place: not a new scan, so neither
        `scanned_at` nor the stats history moves."""
        with session_scope() as s:
            s.execute(
                update(WatchedProfileRow)
                .where(WatchedProfileRow.username == username)
                .values(result=result)
            )

    @invalidates_cache
    def record_scan(
        self, username: str, result: dict[str, Any] | None, error: str | None
    ) -> None:
        """A failed scan keeps the previous result on show, next to its error.
        A successful one also appends its numbers to `profile_scan_stats`, in
        the same transaction (`result` is a dumped `ProfileReels`, its
        computed stats included)."""
        now = time.time()
        values: dict[str, Any] = {"scanned_at": now, "claimed_at": None, "error": error}
        if result is not None:
            values["result"] = result
        with session_scope() as s:
            updated = s.execute(
                update(WatchedProfileRow)
                .where(WatchedProfileRow.username == username)
                .values(**values)
                .returning(WatchedProfileRow.group_name)
            )
            # The stats row carries a group: the first owner's.
            group = next(iter(updated.scalars().all()), None)
            if result is not None:
                s.add(
                    ProfileScanStatRow(
                        username=username,
                        group_name=group,
                        scanned_at=now,
                        window_days=result["window_days"],
                        followers=result["profile"]["followers"],
                        reels=len(result["recent"]),
                        eligible=result["eligible"],
                        views_total=result["views_total"],
                        views_max=result["views_max"],
                        views_median=result["views_median"],
                    )
                )
