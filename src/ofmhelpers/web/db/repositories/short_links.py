"""Links on link providers (/links), their daily numbers, and each user's
sync state per provider. Each link belongs to a user; `owner` limits a call
to that user's rows (None: everyone's, for system work).

Link lists are not cached: they join creators and tracked accounts, whose
writes (/accounts) would not invalidate them. Only the sync state, which
nothing else writes, is."""

from __future__ import annotations

import time
from datetime import date
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, or_, select, update
from sqlalchemy.dialects.postgresql import insert

from ofmhelpers.web.db.models import (
    CreatorRow,
    LinkSyncRow,
    ShortLinkDayRow,
    ShortLinkRow,
    TrackedAccountRow,
)
from ofmhelpers.web.db.repositories.cached_repository import (
    CachedRepository,
    cached,
    invalidates_cache,
)
from ofmhelpers.web.db.repositories.owner_scope import mine
from ofmhelpers.web.db.session import session_scope

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

# The statuses of a link the provider has.
LIVE = ("active", "inactive")

_LINKS = (
    select(ShortLinkRow, CreatorRow.name, TrackedAccountRow.handle)
    .outerjoin(CreatorRow, ShortLinkRow.creator_id == CreatorRow.id)
    .outerjoin(TrackedAccountRow, ShortLinkRow.account_id == TrackedAccountRow.id)
)


def _to_dict(row: ShortLinkRow, creator: str | None, handle: str | None) -> dict:
    return {
        "id": row.id,
        "user_id": row.user_id,
        "provider": row.provider,
        "remote_id": row.remote_id,
        "slug": row.slug,
        "domain": row.domain,
        "url": row.url,
        "destination": row.destination,
        "target": row.target,
        "creator": creator,
        "account_id": row.account_id,
        "account_handle": handle,
        "status": row.status,
        "adult": row.adult,
        "error": row.error,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _days(s: Session, link_ids: list[int], since: date) -> dict[int, list[dict]]:
    found: dict[int, list[dict]] = {}
    if not link_ids:
        return found
    day = ShortLinkDayRow
    rows = s.execute(
        select(day.link_id, day.day, day.views, day.clicks)
        .where(day.link_id.in_(link_ids), day.day >= since)
        .order_by(day.day)
    ).all()
    for link_id, d, views, clicks in rows:
        found.setdefault(link_id, []).append(
            {"day": d.isoformat(), "views": views, "clicks": clicks}
        )
    return found


def _creator_of_account(s: Session, account_id: int | None) -> int | None:
    if account_id is None:
        return None
    return s.execute(
        select(TrackedAccountRow.creator_id).where(TrackedAccountRow.id == account_id)
    ).scalar_one_or_none()


class ShortLinkRepository(CachedRepository):
    cache_namespace = "short_link"

    def list_all(self, owner: int | None, since: date) -> list[dict]:
        """`owner`'s links, newest first, each with its `days` since
        `since`."""
        with session_scope() as s:
            rows = s.execute(
                _LINKS.where(*mine(ShortLinkRow.user_id, owner)).order_by(
                    ShortLinkRow.created_at.desc(), ShortLinkRow.id.desc()
                )
            ).all()
            days = _days(s, [r[0].id for r in rows], since)
            return [
                {**_to_dict(row, creator, handle), "days": days.get(row.id, [])}
                for row, creator, handle in rows
            ]

    def by_ids(self, ids: list[int], owner: int | None = None) -> list[dict]:
        with session_scope() as s:
            rows = s.execute(
                _LINKS.where(
                    ShortLinkRow.id.in_(ids), *mine(ShortLinkRow.user_id, owner)
                ).order_by(ShortLinkRow.id)
            ).all()
            return [_to_dict(*r) for r in rows]

    def taken(self, user_id: int, provider: str) -> set[tuple[str, str]]:
        """(domain, slug) of every link `user_id` has on `provider`."""
        with session_scope() as s:
            rows = s.execute(
                select(ShortLinkRow.domain, ShortLinkRow.slug).where(
                    ShortLinkRow.user_id == user_id, ShortLinkRow.provider == provider
                )
            ).all()
            return {(d, slug) for d, slug in rows}

    def add_creating(self, user_id: int, provider: str, links: list[dict]) -> list[int]:
        """New links, still to be made (status "creating"): `links` carry
        slug, domain, destination, target, account_id; each takes its
        account's creator, or `creator` (a name of `user_id`'s)."""
        now = time.time()
        with session_scope() as s:
            ids = []
            for link in links:
                creator_id = _creator_of_account(s, link["account_id"])
                if creator_id is None and link.get("creator"):
                    creator_id = s.execute(
                        select(CreatorRow.id).where(
                            CreatorRow.user_id == user_id,
                            CreatorRow.name == link["creator"],
                        )
                    ).scalar_one_or_none()
                row = ShortLinkRow(
                    user_id=user_id,
                    provider=provider,
                    slug=link["slug"],
                    domain=link["domain"],
                    url="",
                    destination=link["destination"],
                    target=link["target"],
                    account_id=link["account_id"],
                    creator_id=creator_id,
                    status="creating",
                    created_at=now,
                    updated_at=now,
                )
                s.add(row)
                s.flush()
                ids.append(row.id)
            return ids

    def created(self, link_id: int, remote: dict[str, Any]) -> None:
        """The provider made it: its id, address and state."""
        with session_scope() as s:
            s.execute(
                update(ShortLinkRow)
                .where(ShortLinkRow.id == link_id)
                .values(
                    remote_id=remote["remote_id"],
                    slug=remote["slug"],
                    domain=remote["domain"],
                    url=remote["url"],
                    status="active" if remote["active"] else "inactive",
                    adult=remote["adult"],
                    error=None,
                    updated_at=time.time(),
                )
            )

    def set_status(self, ids: list[int], status: str, error: str | None = None) -> None:
        with session_scope() as s:
            s.execute(
                update(ShortLinkRow)
                .where(ShortLinkRow.id.in_(ids))
                .values(status=status, error=error, updated_at=time.time())
            )

    def set_slug(self, link_id: int, slug: str) -> None:
        with session_scope() as s:
            s.execute(
                update(ShortLinkRow)
                .where(ShortLinkRow.id == link_id)
                .values(slug=slug, updated_at=time.time())
            )

    def set_adult(self, ids: list[int]) -> None:
        with session_scope() as s:
            s.execute(
                update(ShortLinkRow)
                .where(ShortLinkRow.id.in_(ids))
                .values(adult=True, error=None, updated_at=time.time())
            )

    def set_creator(
        self, ids: list[int], creator: str | None, owner: int | None = None
    ) -> int:
        """Assigns the links (of `owner`) to their owner's existing creator
        `creator` (None: unassigned); how many were changed."""
        link = ShortLinkRow
        with session_scope() as s:
            rows = s.execute(
                select(link.id, link.user_id).where(
                    link.id.in_(ids), *mine(link.user_id, owner)
                )
            ).all()
            for link_id, user_id in rows:
                creator_id = None
                if creator:
                    creator_id = s.execute(
                        select(CreatorRow.id).where(
                            CreatorRow.user_id == user_id, CreatorRow.name == creator
                        )
                    ).scalar_one_or_none()
                s.execute(
                    update(link)
                    .where(link.id == link_id)
                    .values(creator_id=creator_id, updated_at=time.time())
                )
            return len(rows)

    def remove(self, ids: list[int]) -> None:
        with session_scope() as s:
            s.execute(delete(ShortLinkRow).where(ShortLinkRow.id.in_(ids)))

    def record_sync(
        self,
        user_id: int,
        provider: str,
        remotes: list[dict],
        days: dict[str, list[dict]],
        accounts: dict[str, int],
        create_ttl_s: float,
    ) -> None:
        """What the provider lists now, in one transaction: each remote link
        updates its row (matched by remote id, else by address: a link whose
        job died after making it), or is added (on `accounts`' Instagram
        account when its slug is one's: slug -> account id); rows it no
        longer lists are deleted ("deleting" ones too: their delete went
        through), "creating" ones older than `create_ttl_s` failed, and
        "deleting" ones it still lists after `create_ttl_s` (their job was
        lost) go back to their status. `days` (remote id -> days) replace the
        stored ones."""
        now = time.time()
        link = ShortLinkRow
        with session_scope() as s:
            rows = list(
                s.execute(
                    select(link).where(
                        link.user_id == user_id, link.provider == provider
                    )
                ).scalars()
            )
            by_remote = {r.remote_id: r for r in rows if r.remote_id}
            by_address = {(r.domain, r.slug): r for r in rows}
            seen: set[int] = set()
            for remote in remotes:
                row = by_remote.get(remote["remote_id"]) or by_address.get(
                    (remote["domain"], remote["slug"])
                )
                if row is None:
                    account_id = accounts.get(remote["slug"])
                    row = ShortLinkRow(
                        user_id=user_id,
                        provider=provider,
                        slug=remote["slug"],
                        domain=remote["domain"],
                        destination=remote["destination"],
                        target=remote["target"],
                        account_id=account_id,
                        creator_id=_creator_of_account(s, account_id),
                        created_at=remote["created_at"] or now,
                    )
                    s.add(row)
                elif row.status == "deleting":
                    seen.add(row.id)
                    if row.updated_at >= now - create_ttl_s:
                        continue
                    # Its delete job was lost (a deploy): the link is still
                    # there, back to what it is with the reason.
                    row.error = "Delete interrupted: delete it again"
                row.remote_id = remote["remote_id"]
                row.slug, row.domain = remote["slug"], remote["domain"]
                row.url, row.destination = remote["url"], remote["destination"]
                row.target = row.target or remote["target"]
                row.status = "active" if remote["active"] else "inactive"
                if remote["adult"] is not None:
                    row.adult = remote["adult"]
                if row.error != "Delete interrupted: delete it again":
                    row.error = None
                row.updated_at = now
                s.flush()
                seen.add(row.id)
                self._replace_days(s, row.id, days.get(remote["remote_id"], []))
            for row in rows:
                if row.id in seen:
                    continue
                if row.status in (*LIVE, "deleting"):
                    # Gone from the provider (a delete that went through).
                    s.delete(row)
                elif row.status == "creating" and row.created_at < now - create_ttl_s:
                    row.status = "failed"
                    row.error = (
                        "Interrupted while being made: remove it and generate again"
                    )
                    row.updated_at = now

    @staticmethod
    def _replace_days(s: Session, link_id: int, days: list[dict]) -> None:
        if not days:
            return
        first = min(date.fromisoformat(d["day"]) for d in days)
        s.execute(
            delete(ShortLinkDayRow).where(
                ShortLinkDayRow.link_id == link_id, ShortLinkDayRow.day >= first
            )
        )
        s.execute(
            insert(ShortLinkDayRow),
            [
                {
                    "link_id": link_id,
                    "day": date.fromisoformat(d["day"]),
                    "views": d["views"],
                    "clicks": d["clicks"],
                }
                for d in days
            ],
        )

    # --- sync state -------------------------------------------------------

    @cached
    def syncs(self, owner: int | None = None) -> list[dict]:
        with session_scope() as s:
            rows = s.execute(
                select(LinkSyncRow).where(*mine(LinkSyncRow.user_id, owner))
            ).scalars()
            return [
                {
                    "user_id": r.user_id,
                    "provider": r.provider,
                    "synced_at": r.synced_at,
                    "claimed_at": r.claimed_at,
                    "error": r.error,
                    "usage": r.usage,
                }
                for r in rows
            ]

    @invalidates_cache
    def claim_due(
        self, pairs: list[tuple[int, str]], interval_s: float, claim_ttl_s: float
    ) -> list[tuple[int, str]]:
        """Of `pairs` (user, provider), those not synced for `interval_s`
        and not claimed within `claim_ttl_s`, claimed in one UPDATE: of two
        sweepers racing, each pair goes to exactly one."""
        if not pairs:
            return []
        now = time.time()
        sync = LinkSyncRow
        with session_scope() as s:
            s.execute(
                insert(sync)
                .values([{"user_id": u, "provider": p} for u, p in pairs])
                .on_conflict_do_nothing()
            )
            claimed = s.execute(
                update(sync)
                .where(
                    or_(
                        *((sync.user_id == u) & (sync.provider == p) for u, p in pairs)
                    ),
                    or_(sync.synced_at.is_(None), sync.synced_at < now - interval_s),
                    or_(sync.claimed_at.is_(None), sync.claimed_at < now - claim_ttl_s),
                )
                .values(claimed_at=now)
                .returning(sync.user_id, sync.provider)
            ).all()
            return sorted((u, p) for u, p in claimed)

    @invalidates_cache
    def mark_due(self, user_id: int, provider: str | None = None) -> None:
        """`user_id`'s syncs (of `provider`, else all) due now, claims
        dropped."""
        sync = LinkSyncRow
        with session_scope() as s:
            s.execute(
                update(sync)
                .where(
                    sync.user_id == user_id,
                    *([sync.provider == provider] if provider else []),
                )
                .values(synced_at=None, claimed_at=None)
            )

    @invalidates_cache
    def finish_sync(
        self,
        user_id: int,
        provider: str,
        usage: dict[str, Any] | None,
        error: str | None,
    ) -> None:
        """A sync's outcome: synced now, claim dropped; a failed one keeps
        the previous quota."""
        values: dict[str, Any] = {
            "synced_at": time.time(),
            "claimed_at": None,
            "error": error,
        }
        if usage is not None:
            values["usage"] = usage
        with session_scope() as s:
            s.execute(
                insert(LinkSyncRow)
                .values(user_id=user_id, provider=provider, **values)
                .on_conflict_do_update(
                    index_elements=["user_id", "provider"], set_=values
                )
            )
