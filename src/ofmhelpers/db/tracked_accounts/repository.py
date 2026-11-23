# Copyright (C) 2026 jpkrajewski

import time
from typing import Any
lazy from collections.abc import Sequence

from pydantic import BaseModel
from sqlalchemy import (
    Float,
    Integer,
    column,
    delete,
    func,
    or_,
    select,
    update,
    values,
)
from sqlalchemy.dialects.postgresql import insert
lazy from sqlalchemy import Row
lazy from sqlalchemy.orm import Session

from ofmhelpers.db.cached_repository import CachedRepository, cached, invalidates_cache
from ofmhelpers.db.models import (
    AccountSnapshotRow,
    CreatorRow,
    ShortLinkRow,
    TrackedAccountRow,
    TrackedPostRow,
)
from ofmhelpers.db.owner_scope import mine
from ofmhelpers.db.session import session_scope
from ofmhelpers.db.tracked_accounts.models import platform_names
from ofmhelpers.db.tracked_accounts.schema import (
    AccountMetrics,
    AccountProfile,
    Baseline,
    PostStat,
    PostViews,
    Snapshot,
    TrackedAccount,
)


def _account(row: TrackedAccountRow, creator: str | None) -> TrackedAccount:
    profile, metrics = row.profile, row.metrics
    names = platform_names().get(row.platform)
    return TrackedAccount(
        id=row.id,
        user_id=row.user_id,
        platform=row.platform,
        handle=row.handle,
        profile_url=names.profile_url(row.handle) if names else "",
        platform_label=names.label if names else "",
        creator=creator,
        notes=row.notes,
        added_at=row.created_at.timestamp(),
        scanned_at=row.scanned_at,
        claimed_at=row.claimed_at,
        untracked_at=row.untracked_at,
        error=row.error,
        profile=None if profile is None else AccountProfile.model_validate(profile),
        metrics=None if metrics is None else AccountMetrics.model_validate(metrics),
        covered_since=row.covered_since,
        last_scan_at=None,
    )


def _creator_id(s: Session, name: str | None, user_id: int) -> int | None:
    if not name:
        return None
    s.execute(
        insert(CreatorRow)
        .values(name=name, user_id=user_id)
        .on_conflict_do_nothing(constraint="uq_creators_owner_name"),
    )
    return s.execute(
        select(CreatorRow.id).where(
            CreatorRow.name == name,
            CreatorRow.user_id == user_id,
        ),
    ).scalar_one()


class _AccountBaseline(BaseModel, frozen=True):
    account_id: int
    followers: int
    taken_at: float


class _PeriodBaseline(_AccountBaseline, frozen=True):
    days: int


class _AccountScan(BaseModel, frozen=True):
    account_id: int
    taken_at: float


class _AccountPostViews(BaseModel, frozen=True):
    account_id: int
    post: PostViews


def _last_scans(s: Session, account_id: int | None) -> list[_AccountScan]:
    snap = AccountSnapshotRow
    query = select(snap.account_id, func.max(snap.taken_at)).group_by(snap.account_id)
    if account_id is not None:
        query = query.where(snap.account_id == account_id)
    return [_AccountScan(account_id=a, taken_at=t) for a, t in s.execute(query)]


def _first_baselines(s: Session, account_id: int | None) -> list[_AccountBaseline]:
    snap = AccountSnapshotRow
    query = (
        select(snap.account_id, snap.followers, snap.taken_at)
        .distinct(snap.account_id)
        .where(snap.followers.is_not(None))
        .order_by(snap.account_id, snap.taken_at.asc())
    )
    if account_id is not None:
        query = query.where(snap.account_id == account_id)
    return [
        _AccountBaseline(account_id=a, followers=f, taken_at=t)
        for a, f, t in s.execute(query).all()
    ]


def _period_baselines(
    s: Session,
    cutoffs: dict[int, float],
    account_id: int | None,
) -> list[_PeriodBaseline]:
    if not cutoffs:
        return []
    snap = AccountSnapshotRow
    asked = values(column("days", Integer), column("cutoff", Float), name="asked").data(
        list(cutoffs.items()),
    )
    query = (
        select(asked.c.days, snap.account_id, snap.followers, snap.taken_at)
        .join(asked, snap.taken_at <= asked.c.cutoff)
        .distinct(asked.c.days, snap.account_id)
        .where(snap.followers.is_not(None))
        .order_by(asked.c.days, snap.account_id, snap.taken_at.desc())
    )
    if account_id is not None:
        query = query.where(snap.account_id == account_id)
    return [
        _PeriodBaseline(days=d, account_id=a, followers=f, taken_at=t)
        for d, a, f, t in s.execute(query).all()
    ]


def _post_views(s: Session, account_id: int | None) -> list[_AccountPostViews]:
    post = TrackedPostRow
    query = select(post.account_id, post.views, post.posted_at)
    if account_id is not None:
        query = query.where(post.account_id == account_id)
    return [
        _AccountPostViews(
            account_id=a,
            post=PostViews(views=views, posted_at=posted_at),
        )
        for a, views, posted_at in s.execute(query).all()
    ]


def _upsert_posts(s: Session, account_id: int, posts: list[dict], now: float) -> None:
    if not posts:
        return
    rows = [
        {
            "account_id": account_id,
            "post_id": p["id"],
            "url": p["url"],
            "thumbnail_url": p.get("thumbnail_url", ""),
            "views": p["views"],
            "posted_at": p["posted_at"],
            "first_seen_at": now,
            "last_seen_at": now,
        }
        for p in posts
    ]
    stmt = insert(TrackedPostRow).values(rows)
    s.execute(
        stmt.on_conflict_do_update(
            constraint="uq_tracked_posts_account_post",
            set_={
                c: stmt.excluded[c]
                for c in ("url", "thumbnail_url", "views", "posted_at", "last_seen_at")
            },
        ),
    )


def _last_success(s: Session, account_id: int) -> float | None:
    snap = AccountSnapshotRow
    return s.execute(
        select(func.max(snap.taken_at)).where(snap.account_id == account_id),
    ).scalar_one()


def _covered_since(
    s: Session,
    row: TrackedAccountRow,
    reach: float | None,
    cutoff: float,
) -> float | None:
    if reach is None:
        return row.covered_since
    last = _last_success(s, row.id)
    if row.covered_since is not None and last is not None and reach <= last:
        return max(min(row.covered_since, reach), cutoff)
    return max(reach, cutoff)


def _merge_rename(
    s: Session,
    row: TrackedAccountRow,
    platform_account_id: str | None,
) -> TrackedAccountRow:
    if platform_account_id is None:
        return row
    acc = TrackedAccountRow
    older = s.execute(
        select(acc)
        .where(
            acc.user_id == row.user_id,
            acc.platform == row.platform,
            acc.platform_account_id == platform_account_id,
            acc.id != row.id,
        )
        .with_for_update(),
    ).scalar_one_or_none()
    if older is None:
        row.platform_account_id = platform_account_id
        return row
    s.execute(
        update(AccountSnapshotRow)
        .where(AccountSnapshotRow.account_id == row.id)
        .values(account_id=older.id),
    )
    s.execute(
        update(ShortLinkRow)
        .where(ShortLinkRow.account_id == row.id)
        .values(account_id=older.id),
    )
    moved = s.execute(
        select(TrackedPostRow).where(TrackedPostRow.account_id == row.id),
    ).scalars()
    _upsert_posts(
        s,
        older.id,
        [
            {
                "id": p.post_id,
                "url": p.url,
                "thumbnail_url": p.thumbnail_url,
                "views": p.views,
                "posted_at": p.posted_at,
            }
            for p in moved
        ],
        time.time(),
    )
    older.creator_id = older.creator_id or row.creator_id
    older.notes = "\n".join(n for n in (older.notes, row.notes) if n)
    handle, scanned_at = row.handle, row.scanned_at
    s.delete(row)
    s.flush()
    older.handle, older.untracked_at = handle, None
    older.scanned_at, older.claimed_at, older.error = scanned_at, None, None
    return older


_ACCOUNTS = select(TrackedAccountRow, CreatorRow.name).outerjoin(
    CreatorRow,
    TrackedAccountRow.creator_id == CreatorRow.id,
)


def _with_baselines(
    s: Session,
    rows: Sequence[Row],
    periods_days: list[int],
    account_id: int | None,
) -> list[TrackedAccount]:
    now = time.time()
    first = {b.account_id: b for b in _first_baselines(s, account_id)}
    last_scan = {x.account_id: x.taken_at for x in _last_scans(s, account_id)}
    cutoffs = {d: now - d * 86400 for d in periods_days}
    periods: dict[int, dict[int, _AccountBaseline]] = {d: {} for d in cutoffs}
    for found in _period_baselines(s, cutoffs, account_id):
        periods[found.days][found.account_id] = found
    post_views: dict[int, list[PostViews]] = {}
    for entry in _post_views(s, account_id):
        post_views.setdefault(entry.account_id, []).append(entry.post)
    out = []
    for row, creator in rows:
        item = _account(row, creator)
        item.post_views = post_views.get(row.id, [])
        item.baselines = [
            Baseline(days=d, followers=base.followers, taken_at=base.taken_at)
            for d in periods_days
            if (base := periods[d].get(row.id))
        ]
        item.first = _snapshot_baseline(first.get(row.id))
        item.last_scan_at = last_scan.get(row.id)
        out.append(item)
    return out


def _snapshot_baseline(snap: _AccountBaseline | None) -> Baseline | None:
    if snap is None:
        return None
    return Baseline(days=0, followers=snap.followers, taken_at=snap.taken_at)


class TrackedAccountRepository(CachedRepository):
    cache_namespace = "tracked_account"

    @invalidates_cache
    def add(
        self,
        platform: str,
        handle: str,
        creator: str | None,
        user_id: int,
        revive: bool = True,  # noqa: FBT001, FBT002
    ) -> bool:
        acc = TrackedAccountRow
        same = (acc.user_id == user_id, acc.platform == platform, acc.handle == handle)
        with session_scope() as s:
            creator_id = _creator_id(s, creator, user_id)
            added = s.execute(
                insert(TrackedAccountRow)
                .values(
                    user_id=user_id,
                    platform=platform,
                    handle=handle,
                    creator_id=creator_id,
                    notes="",
                )
                .on_conflict_do_nothing(
                    constraint="uq_tracked_accounts_platform_handle",
                )
                .returning(TrackedAccountRow.id),
            ).scalar_one_or_none()
            if added is not None:
                return True
            if creator_id is not None:
                s.execute(update(acc).where(*same).values(creator_id=creator_id))
            if not revive:
                return False
            restored = s.execute(
                update(acc)
                .where(*same, acc.untracked_at.is_not(None))
                .values(
                    untracked_at=None,
                    scanned_at=None,
                    claimed_at=None,
                    failed_scans=0,
                )
                .returning(acc.id),
            ).scalar_one_or_none()
            return restored is not None

    @cached
    def list_all(
        self,
        periods_days: list[int],
        owner: int | None = None,
    ) -> list[TrackedAccount]:
        with session_scope() as s:
            rows = s.execute(
                _ACCOUNTS.where(
                    TrackedAccountRow.untracked_at.is_(None),
                    *mine(TrackedAccountRow.user_id, owner),
                ).order_by(
                    TrackedAccountRow.created_at.desc(),
                    TrackedAccountRow.id.desc(),
                ),
            ).all()
            return _with_baselines(s, rows, periods_days, None)

    @cached
    def get(self, account_id: int, periods_days: list[int]) -> TrackedAccount | None:
        with session_scope() as s:
            rows = s.execute(_ACCOUNTS.where(TrackedAccountRow.id == account_id)).all()
            if not rows:
                return None
            item = _with_baselines(s, rows, periods_days, account_id)
            post = TrackedPostRow
            posts = s.execute(
                select(post)
                .where(post.account_id == account_id)
                .order_by(post.posted_at.desc()),
            ).scalars()
            item[0].posts = [
                PostStat(
                    id=p.post_id,
                    url=p.url,
                    views=p.views,
                    posted_at=p.posted_at,
                    thumbnail_url=p.thumbnail_url,
                )
                for p in posts
            ]
            return item[0]

    @cached
    def history(self, account_id: int, since: float) -> list[Snapshot]:
        snap = AccountSnapshotRow
        with session_scope() as s:
            rows = s.execute(
                select(snap.taken_at, snap.followers, snap.metrics)
                .where(
                    snap.account_id == account_id,
                    snap.taken_at >= since,
                    snap.followers.is_not(None),
                )
                .order_by(snap.taken_at),
            ).all()
            return [Snapshot(taken_at=t, followers=f, metrics=m) for t, f, m in rows]

    @cached
    def list_creators(self, owner: int | None = None) -> list[str]:
        with session_scope() as s:
            return sorted(
                set(
                    s.execute(
                        select(CreatorRow.name).where(*mine(CreatorRow.user_id, owner)),
                    ).scalars(),
                ),
            )

    @invalidates_cache
    def set_creator(
        self,
        account_ids: list[int],
        creator: str | None,
        owner: int | None = None,
    ) -> int:
        acc = TrackedAccountRow
        with session_scope() as s:
            rows = s.execute(
                select(acc.id, acc.user_id).where(
                    acc.id.in_(account_ids),
                    *mine(acc.user_id, owner),
                ),
            ).all()
            for account_id, user_id in rows:
                s.execute(
                    update(acc)
                    .where(acc.id == account_id)
                    .values(creator_id=_creator_id(s, creator, user_id)),
                )
            return len(rows)

    @invalidates_cache
    def set_notes(self, account_id: int, notes: str, owner: int | None = None) -> bool:
        with session_scope() as s:
            updated = s.execute(
                update(TrackedAccountRow)
                .where(
                    TrackedAccountRow.id == account_id,
                    *mine(TrackedAccountRow.user_id, owner),
                )
                .values(notes=notes)
                .returning(TrackedAccountRow.id),
            )
            return updated.scalar_one_or_none() is not None

    @invalidates_cache
    def untrack(self, account_ids: list[int], owner: int | None = None) -> int:
        acc = TrackedAccountRow
        with session_scope() as s:
            untracked = s.execute(
                update(acc)
                .where(
                    acc.id.in_(account_ids),
                    acc.untracked_at.is_(None),
                    *mine(acc.user_id, owner),
                )
                .values(untracked_at=time.time(), claimed_at=None)
                .returning(acc.id),
            )
            return len(untracked.all())

    @invalidates_cache
    def mark_due(self, account_ids: list[int]) -> None:
        with session_scope() as s:
            s.execute(
                update(TrackedAccountRow)
                .where(TrackedAccountRow.id.in_(account_ids))
                .values(scanned_at=None, claimed_at=None, failed_scans=0),
            )

    @invalidates_cache
    def make_due(self, owner: int | None, claim_ttl_s: float) -> list[int]:
        acc = TrackedAccountRow
        now = time.time()
        with session_scope() as s:
            made = s.execute(
                update(acc)
                .where(
                    acc.untracked_at.is_(None),
                    *mine(acc.user_id, owner),
                    or_(acc.claimed_at.is_(None), acc.claimed_at < now - claim_ttl_s),
                )
                .values(scanned_at=None, claimed_at=None, failed_scans=0)
                .returning(acc.id),
            )
            return sorted(made.scalars().all())

    @invalidates_cache
    def claim_due(
        self,
        interval_s: float,
        claim_ttl_s: float,
        max_interval_s: float,
    ) -> list[int]:
        now = time.time()
        acc = TrackedAccountRow
        backoff = func.least(
            interval_s * func.power(2, acc.failed_scans),
            max(interval_s, max_interval_s),
        )
        with session_scope() as s:
            claimed = s.execute(
                update(acc)
                .where(
                    acc.untracked_at.is_(None),
                    or_(acc.scanned_at.is_(None), acc.scanned_at < now - backoff),
                    or_(acc.claimed_at.is_(None), acc.claimed_at < now - claim_ttl_s),
                )
                .values(claimed_at=now)
                .returning(acc.id),
            )
            return sorted(claimed.scalars().all())

    @invalidates_cache
    def record_scan(  # noqa: PLR0913, PLR0917
        self,
        account_id: int,
        profile: dict[str, Any] | None,
        metrics: dict[str, Any] | None,
        posts: list[dict],
        reach: float | None,
        retention_days: int,
        error: str | None,
    ) -> None:
        now = time.time()
        acc = TrackedAccountRow
        with session_scope() as s:
            row = s.execute(
                select(acc).where(acc.id == account_id).with_for_update(),
            ).scalar_one_or_none()
            if row is None:
                return
            row.scanned_at, row.claimed_at, row.error = now, None, error
            if metrics is None or profile is None:
                row.failed_scans += 1
                return
            row = _merge_rename(s, row, profile.get("account_id") or None)
            cutoff = now - retention_days * 86400
            row.covered_since = _covered_since(s, row, reach, cutoff)
            row.profile, row.metrics, row.failed_scans = profile, metrics, 0
            kept = [p for p in posts if p["posted_at"] >= cutoff]
            _upsert_posts(s, row.id, kept, now)
            s.add(
                AccountSnapshotRow(
                    account_id=row.id,
                    taken_at=now,
                    followers=(
                        None
                        if profile.get("followers_hidden")
                        else metrics["followers"]
                    ),
                    metrics=metrics,
                ),
            )
            s.execute(
                delete(TrackedPostRow).where(
                    TrackedPostRow.account_id == row.id,
                    TrackedPostRow.posted_at < cutoff,
                ),
            )
