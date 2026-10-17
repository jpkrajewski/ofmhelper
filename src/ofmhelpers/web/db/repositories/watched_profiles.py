"""Instagram profiles /reels rescans, and each one's latest scan."""

from __future__ import annotations

import time
from typing import Any

from sqlalchemy import or_, select, update
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
from ofmhelpers.web.db.session import session_scope


def _to_dict(row: WatchedProfileRow) -> dict:
    return {
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
    def add(self, username: str, group: str | None) -> bool:
        """True when the profile is new. Adding one already watched only moves
        it to `group`, when a group is given."""
        with session_scope() as s:
            existing = s.get(WatchedProfileRow, username)
            if existing is not None:
                if group is not None:
                    existing.group_name = group
                return False
            added = s.execute(
                insert(WatchedProfileRow)
                .values(username=username, group_name=group, added_at=time.time())
                .on_conflict_do_nothing(index_elements=["username"])
                .returning(WatchedProfileRow.username)
            )
            return added.scalar_one_or_none() is not None

    @cached
    def list_all(self) -> list[dict]:
        """Oldest first, so a newly added profile lands at the bottom."""
        with session_scope() as s:
            rows = (
                s.execute(
                    select(WatchedProfileRow).order_by(WatchedProfileRow.added_at)
                )
                .scalars()
                .all()
            )
            return [_to_dict(r) for r in rows]

    @invalidates_cache
    def create_group(self, name: str) -> None:
        """Creating a group that exists already is a no-op."""
        with session_scope() as s:
            s.execute(
                insert(ProfileGroupRow)
                .values(name=name, created_at=time.time())
                .on_conflict_do_nothing(index_elements=["name"])
            )

    @cached
    def list_groups(self) -> list[str]:
        """Every group: created ones, empty or not, and any a profile is in."""
        with session_scope() as s:
            created = s.execute(select(ProfileGroupRow.name)).scalars().all()
            used = (
                s.execute(
                    select(WatchedProfileRow.group_name)
                    .where(WatchedProfileRow.group_name.is_not(None))
                    .distinct()
                )
                .scalars()
                .all()
            )
            return sorted({*created, *(g for g in used if g)})

    @invalidates_cache
    def set_group(self, username: str, group: str | None) -> bool:
        with session_scope() as s:
            moved = s.execute(
                update(WatchedProfileRow)
                .where(WatchedProfileRow.username == username)
                .values(group_name=group)
                .returning(WatchedProfileRow.username)
            )
            return moved.scalar_one_or_none() is not None

    @invalidates_cache
    def delete(self, username: str) -> bool:
        with session_scope() as s:
            row = s.get(WatchedProfileRow, username)
            if row is None:
                return False
            s.delete(row)
            return True

    @invalidates_cache
    def claim_due(self, interval_s: float, claim_ttl_s: float) -> list[str]:
        """Marks every profile not scanned for `interval_s` (and not already
        claimed within `claim_ttl_s`) as claimed, in one UPDATE, and returns
        them: of two sweepers racing, each profile goes to exactly one."""
        now = time.time()
        with session_scope() as s:
            claimed = s.execute(
                update(WatchedProfileRow)
                .where(
                    or_(
                        WatchedProfileRow.scanned_at.is_(None),
                        WatchedProfileRow.scanned_at < now - interval_s,
                    ),
                    or_(
                        WatchedProfileRow.claimed_at.is_(None),
                        WatchedProfileRow.claimed_at < now - claim_ttl_s,
                    ),
                )
                .values(claimed_at=now)
                .returning(WatchedProfileRow.username)
            )
            return sorted(claimed.scalars().all())

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
            group = updated.scalar_one_or_none()
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
