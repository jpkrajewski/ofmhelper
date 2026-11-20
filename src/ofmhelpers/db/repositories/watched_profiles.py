import time
from typing import Any

from sqlalchemy import ColumnElement, delete, or_, select, update
from sqlalchemy.dialects.postgresql import insert

from ofmhelpers.db.models import ProfileGroupRow, ProfileScanStatRow, WatchedProfileRow
from ofmhelpers.db.repositories.cached_repository import (
    CachedRepository,
    cached,
    invalidates_cache,
)
from ofmhelpers.db.repositories.owner_scope import mine
from ofmhelpers.db.session import session_scope
from ofmhelpers.schemas.watched import WatchedProfile


def _profile(row: WatchedProfileRow) -> WatchedProfile:
    return WatchedProfile.model_validate(
        {
            "user_id": row.user_id,
            "username": row.username,
            "group": row.group_name,
            "added_at": row.added_at,
            "scanned_at": row.scanned_at,
            "error": row.error,
            "result": row.result,
        }
    )


class WatchedProfileRepository(CachedRepository):
    cache_namespace = "watched_profile"

    @invalidates_cache
    def add(self, username: str, group: str | None, user_id: int) -> bool:
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
    def list_all(self, owner: int | None = None) -> list[WatchedProfile]:
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
            return [_profile(r) for r in rows]

    @invalidates_cache
    def create_group(self, name: str, user_id: int) -> None:
        with session_scope() as s:
            s.execute(
                insert(ProfileGroupRow)
                .values(user_id=user_id, name=name)
                .on_conflict_do_nothing(index_elements=["user_id", "name"])
            )

    @cached
    def list_groups(self, owner: int | None = None) -> list[str]:
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
