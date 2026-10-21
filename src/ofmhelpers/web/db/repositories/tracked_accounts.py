"""Social accounts /accounts tracks, their scan history, and creators."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert

from ofmhelpers.web.db.models import (
    AccountSnapshotRow,
    CreatorRow,
    TrackedAccountRow,
    TrackedPostRow,
)
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
        "untracked_at": row.untracked_at,
        "error": row.error,
        "profile": row.profile,
        "metrics": row.metrics,
        "covered_since": row.covered_since,
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


def _post_views(s: Session, account_id: int | None) -> dict[int, list[dict]]:
    """Per account (or only `account_id`), its kept posts: views and publish
    time only, what view windows need. Retention is applied when a scan
    succeeds, never on read: an account that stopped syncing (banned,
    untracked) keeps showing what its last successful sync saw."""
    post = TrackedPostRow
    query = select(post.account_id, post.views, post.posted_at)
    if account_id is not None:
        query = query.where(post.account_id == account_id)
    out: dict[int, list[dict]] = {}
    for a, views, posted_at in s.execute(query).all():
        out.setdefault(a, []).append({"views": views, "posted_at": posted_at})
    return out


def _upsert_posts(s: Session, account_id: int, posts: list[dict], now: float) -> None:
    """Inserts the scan's posts; one already kept (same platform id) gets
    its latest views, URLs and `last_seen_at` instead of a duplicate."""
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
        )
    )


def _last_success(s: Session, account_id: int) -> float | None:
    """When the account's newest snapshot (its last successful scan) was
    taken; None before the first."""
    snap = AccountSnapshotRow
    return s.execute(
        select(func.max(snap.taken_at)).where(snap.account_id == account_id)
    ).scalar_one()


def _covered_since(
    s: Session, row: TrackedAccountRow, reach: float | None, cutoff: float
) -> float | None:
    """The publish time the kept posts are complete from after this scan.
    A scan reaching back to the previous successful one (`reach`: the time
    it read every post back to) extends the coverage before it; one that
    does not (a gap: untracked, banned, a capped read after a long pause)
    starts it over at its own reach, since posts in the gap were never
    read. Never older than `cutoff`: pruned posts are not there to count."""
    if reach is None:
        return row.covered_since
    last = _last_success(s, row.id)
    if row.covered_since is not None and last is not None and reach <= last:
        return max(min(row.covered_since, reach), cutoff)
    return max(reach, cutoff)


def _merge_rename(
    s: Session, row: TrackedAccountRow, platform_account_id: str | None
) -> TrackedAccountRow:
    """`row` scanned as the account the platform calls
    `platform_account_id`. When an older row already carries that id, the
    account was renamed and added again under its new handle: the older row
    takes the new handle and `row`'s snapshots, posts, creator and notes,
    and `row` -- the duplicate -- goes. Returns the row the scan belongs
    to. Platforms without ids (None) never merge."""
    if platform_account_id is None:
        return row
    acc = TrackedAccountRow
    older = s.execute(
        select(acc)
        .where(
            acc.platform == row.platform,
            acc.platform_account_id == platform_account_id,
            acc.id != row.id,
        )
        .with_for_update()
    ).scalar_one_or_none()
    if older is None:
        row.platform_account_id = platform_account_id
        return row
    s.execute(
        update(AccountSnapshotRow)
        .where(AccountSnapshotRow.account_id == row.id)
        .values(account_id=older.id)
    )
    moved = s.execute(
        select(TrackedPostRow).where(TrackedPostRow.account_id == row.id)
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


# Accounts with their creator's name.
_ACCOUNTS = select(TrackedAccountRow, CreatorRow.name).outerjoin(
    CreatorRow, TrackedAccountRow.creator_id == CreatorRow.id
)


def _with_baselines(
    s: Session,
    rows: Sequence[Row],
    periods_days: list[int],
    account_id: int | None,
) -> list[dict]:
    """The `_ACCOUNTS` results as dicts, each with `baselines`: per
    period, the newest snapshot at least that old (none for an account
    tracked for less), `first`: its first snapshot, for growth since it
    was added, `latest`: its newest one (the last successful sync), and
    `post_views`: its kept posts' views and publish times."""
    now = time.time()
    first = _baselines(s, None, account_id)
    latest = _baselines(s, now, account_id)
    periods = {d: _baselines(s, now - d * 86400, account_id) for d in periods_days}
    post_views = _post_views(s, account_id)
    out = []
    for row, creator in rows:
        item = _to_dict(row, creator)
        item["post_views"] = post_views.get(row.id, [])
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
    def add(
        self, platform: str, handle: str, creator: str | None, revive: bool = True
    ) -> bool:
        """True when the account is new, or was untracked and is tracked
        again -- the same row, its whole history kept, due for a scan now.
        Adding one already tracked only assigns it to `creator`, when one
        is given."""
        acc = TrackedAccountRow
        same = (acc.platform == platform, acc.handle == handle)
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
                    untracked_at=None, scanned_at=None, claimed_at=None, failed_scans=0
                )
                .returning(acc.id)
            ).scalar_one_or_none()
            return restored is not None

    @cached
    def list_all(self, periods_days: list[int]) -> list[dict]:
        """Every tracked account, newest first, with its growth baselines
        and its posts' views (see `_with_baselines`)."""
        with session_scope() as s:
            rows = s.execute(
                _ACCOUNTS.where(TrackedAccountRow.untracked_at.is_(None)).order_by(
                    TrackedAccountRow.added_at.desc(), TrackedAccountRow.id.desc()
                )
            ).all()
            return _with_baselines(s, rows, periods_days, None)

    @cached
    def get(self, account_id: int, periods_days: list[int]) -> dict | None:
        """One account, tracked or not, with its baselines and its kept
        posts, in full."""
        with session_scope() as s:
            rows = s.execute(_ACCOUNTS.where(TrackedAccountRow.id == account_id)).all()
            if not rows:
                return None
            item = _with_baselines(s, rows, periods_days, account_id)
            post = TrackedPostRow
            posts = s.execute(
                select(post)
                .where(post.account_id == account_id)
                .order_by(post.posted_at.desc())
            ).scalars()
            return {
                **item[0],
                "posts": [
                    {
                        "id": p.post_id,
                        "url": p.url,
                        "views": p.views,
                        "posted_at": p.posted_at,
                        "thumbnail_url": p.thumbnail_url,
                    }
                    for p in posts
                ],
            }

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
    def untrack(self, account_ids: list[int]) -> int:
        """Stops scanning the accounts and hides them from the list; every
        record (profile, posts, snapshots, notes) stays, and adding the
        handle again resumes the same account. How many were tracked."""
        acc = TrackedAccountRow
        with session_scope() as s:
            untracked = s.execute(
                update(acc)
                .where(acc.id.in_(account_ids), acc.untracked_at.is_(None))
                .values(untracked_at=time.time(), claimed_at=None)
                .returning(acc.id)
            )
            return len(untracked.all())

    @invalidates_cache
    def mark_due(self, account_ids: list[int]) -> None:
        """Makes the accounts due now (and drops any claim), so the next
        sweep scans them."""
        with session_scope() as s:
            s.execute(
                update(TrackedAccountRow)
                .where(TrackedAccountRow.id.in_(account_ids))
                .values(scanned_at=None, claimed_at=None, failed_scans=0)
            )

    @invalidates_cache
    def claim_due(
        self, interval_s: float, claim_ttl_s: float, max_interval_s: float
    ) -> list[int]:
        """Marks every tracked account not scanned for its interval (and
        not claimed within `claim_ttl_s`) as claimed, in one UPDATE, and
        returns their ids: of two sweepers racing, each account goes to
        exactly one. The interval is `interval_s`, doubled per consecutive
        failed scan, up to `max_interval_s`."""
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
                .returning(acc.id)
            )
            return sorted(claimed.scalars().all())

    @invalidates_cache
    def record_scan(
        self,
        account_id: int,
        profile: dict[str, Any] | None,
        metrics: dict[str, Any] | None,
        posts: list[dict],
        reach: float | None,
        retention_days: int,
        error: str | None,
    ) -> None:
        """One transaction, one clock, the account's row locked (concurrent
        scans of one account serialize).

        A failed scan (no `metrics`) only records its error and counts the
        failure: every number, post and snapshot stays as the last
        successful sync left it (a banned account keeps its data until it
        is reachable again).

        A successful one: a renamed account (another row already carries
        the platform's id for it) is merged into that row first (see
        `_merge_rename`); then the posts it read within retention are
        upserted, coverage updated (see `_covered_since`), a snapshot
        appended, and posts older than retention pruned -- by age only,
        never because a scan did not return them."""
        now = time.time()
        acc = TrackedAccountRow
        with session_scope() as s:
            row = s.execute(
                select(acc).where(acc.id == account_id).with_for_update()
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
                    followers=metrics["followers"],
                    metrics=metrics,
                )
            )
            s.execute(
                delete(TrackedPostRow).where(
                    TrackedPostRow.account_id == row.id,
                    TrackedPostRow.posted_at < cutoff,
                )
            )
