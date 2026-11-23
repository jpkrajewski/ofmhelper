# Copyright (C) 2026 jpkrajewski

import time
from datetime import UTC, date, datetime, timedelta
from typing import Any

from pydantic import BaseModel
from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
lazy from sqlalchemy.orm import Session

from ofmhelpers.db.cached_repository import CachedRepository, cached, invalidates_cache
from ofmhelpers.db.devices.repository import DuplicateError
from ofmhelpers.db.models import (
    CreatorRow,
    LinkSyncRow,
    SelfLinkDomainRow,
    ShortLinkDayRow,
    ShortLinkRow,
    TrackedAccountRow,
)
from ofmhelpers.db.owner_scope import mine
from ofmhelpers.db.session import session_scope
from ofmhelpers.db.short_links.catalog import DayStat
from ofmhelpers.db.short_links.models import LinkSync, SelfLink, ShortLink
from ofmhelpers.db.short_links.schema import BridgeTarget, LinkAddress, LinkSyncKey
lazy from ofmhelpers.db.short_links.schema import PlannedLink

LIVE = ("active", "inactive")

_LINKS = (
    select(ShortLinkRow, CreatorRow.name, TrackedAccountRow.handle)
    .outerjoin(CreatorRow, ShortLinkRow.creator_id == CreatorRow.id)
    .outerjoin(TrackedAccountRow, ShortLinkRow.account_id == TrackedAccountRow.id)
)


def _link(
    row: ShortLinkRow,
    creator: str | None,
    handle: str | None,
    days: list[DayStat] | None = None,
) -> ShortLink:
    return ShortLink.model_validate(
        {
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
            "created_at": row.created_at.timestamp(),
            "days": days or [],
        },
    )


class _LinkDay(BaseModel, frozen=True):
    link_id: int
    stat: DayStat


def _days(s: Session, link_ids: list[int], since: date) -> list[_LinkDay]:
    if not link_ids:
        return []
    day = ShortLinkDayRow
    rows = s.execute(
        select(day.link_id, day.day, day.views, day.clicks)
        .where(day.link_id.in_(link_ids), day.day >= since)
        .order_by(day.day),
    ).all()
    return [
        _LinkDay(link_id=link_id, stat=DayStat(day=d, views=views, clicks=clicks))
        for link_id, d, views, clicks in rows
    ]


def _taken(exc: IntegrityError) -> None:
    text = str(exc.orig)
    if "uq_short_links_address" in text or "uq_short_links_self_address" in text:
        msg = "That address is already taken"
        raise DuplicateError(msg) from exc


def _creator_of_account(s: Session, account_id: int | None) -> int | None:
    if account_id is None:
        return None
    return s.execute(
        select(TrackedAccountRow.creator_id).where(TrackedAccountRow.id == account_id),
    ).scalar_one_or_none()


class ShortLinkRepository(CachedRepository):
    cache_namespace = "short_link"

    def list_all(self, owner: int | None, since: date) -> list[ShortLink]:
        with session_scope() as s:
            rows = s.execute(
                _LINKS.where(*mine(ShortLinkRow.user_id, owner)).order_by(
                    ShortLinkRow.created_at.desc(),
                    ShortLinkRow.id.desc(),
                ),
            ).all()
            days: dict[int, list[DayStat]] = {}
            for entry in _days(s, [r[0].id for r in rows], since):
                days.setdefault(entry.link_id, []).append(entry.stat)
            return [
                _link(row, creator, handle, days.get(row.id))
                for row, creator, handle in rows
            ]

    def by_ids(self, ids: list[int], owner: int | None = None) -> list[ShortLink]:
        with session_scope() as s:
            rows = s.execute(
                _LINKS.where(
                    ShortLinkRow.id.in_(ids),
                    *mine(ShortLinkRow.user_id, owner),
                ).order_by(ShortLinkRow.id),
            ).all()
            return [_link(*r) for r in rows]

    def active_self_link(self, host: str, slug: str) -> BridgeTarget | None:
        link, dom = ShortLinkRow, SelfLinkDomainRow
        with session_scope() as s:
            row = s.execute(
                select(link.id, link.destination, link.slug, link.domain)
                .join(dom, dom.host == link.domain)
                .where(
                    link.provider == "self",
                    link.domain == host,
                    link.slug == slug,
                    link.status == "active",
                    dom.status == "active",
                    or_(dom.owner_id.is_(None), dom.owner_id == link.user_id),
                )
                .order_by(link.id)
                .limit(1),
            ).first()
            if row is None:
                return None
            return BridgeTarget(
                id=row.id,
                destination=row.destination,
                slug=row.slug,
                domain=row.domain,
            )

    def provider_links(
        self,
        user_id: int,
        provider: str,
        statuses: tuple[str, ...],
    ) -> list[SelfLink]:
        link = ShortLinkRow
        with session_scope() as s:
            rows = s.execute(
                select(link).where(
                    link.user_id == user_id,
                    link.provider == provider,
                    link.status.in_(statuses),
                ),
            ).scalars()
            return [
                SelfLink(
                    remote_id=r.remote_id,
                    slug=r.slug,
                    domain=r.domain,
                    url=r.url,
                    destination=r.destination,
                    active=r.status == "active",
                    adult=r.adult,
                    created_at=r.created_at.timestamp(),
                    id=r.id,
                )
                for r in rows
            ]

    def taken(self, user_id: int, provider: str) -> list[LinkAddress]:
        with session_scope() as s:
            rows = s.execute(
                select(ShortLinkRow.domain, ShortLinkRow.slug).where(
                    ShortLinkRow.user_id == user_id,
                    ShortLinkRow.provider == provider,
                ),
            ).all()
            return [LinkAddress(domain=d, slug=slug) for d, slug in rows]

    def slugs_on_domain(self, provider: str, domain: str) -> list[str]:
        with session_scope() as s:
            return list(
                s.execute(
                    select(ShortLinkRow.slug).where(
                        ShortLinkRow.provider == provider,
                        ShortLinkRow.domain == domain,
                    ),
                ).scalars(),
            )

    def add_creating(
        self,
        user_id: int,
        provider: str,
        links: list[PlannedLink],
    ) -> list[int]:
        with session_scope() as s:
            ids = []
            for link in links:
                creator_id = _creator_of_account(s, link.account_id)
                if creator_id is None and link.creator:
                    creator_id = s.execute(
                        select(CreatorRow.id).where(
                            CreatorRow.user_id == user_id,
                            CreatorRow.name == link.creator,
                        ),
                    ).scalar_one_or_none()
                row = ShortLinkRow(
                    user_id=user_id,
                    provider=provider,
                    slug=link.slug,
                    domain=link.domain,
                    url="",
                    destination=link.destination,
                    target=link.target,
                    account_id=link.account_id,
                    creator_id=creator_id,
                    status="creating",
                )
                s.add(row)
                try:
                    s.flush()
                except IntegrityError as exc:
                    _taken(exc)
                    raise
                ids.append(row.id)
            return ids

    def created(self, link_id: int, remote: dict[str, Any]) -> None:
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
                ),
            )

    def set_status(self, ids: list[int], status: str, error: str | None = None) -> None:
        with session_scope() as s:
            s.execute(
                update(ShortLinkRow)
                .where(ShortLinkRow.id.in_(ids))
                .values(status=status, error=error),
            )

    def set_slug(self, link_id: int, slug: str) -> None:
        with session_scope() as s:
            s.execute(
                update(ShortLinkRow)
                .where(ShortLinkRow.id == link_id)
                .values(slug=slug),
            )

    def set_adult(self, ids: list[int]) -> None:
        with session_scope() as s:
            s.execute(
                update(ShortLinkRow)
                .where(ShortLinkRow.id.in_(ids))
                .values(adult=True, error=None),
            )

    def set_creator(
        self,
        ids: list[int],
        creator: str | None,
        owner: int | None = None,
    ) -> int:
        link = ShortLinkRow
        with session_scope() as s:
            rows = s.execute(
                select(link.id, link.user_id).where(
                    link.id.in_(ids),
                    *mine(link.user_id, owner),
                ),
            ).all()
            for link_id, user_id in rows:
                creator_id = None
                if creator:
                    creator_id = s.execute(
                        select(CreatorRow.id).where(
                            CreatorRow.user_id == user_id,
                            CreatorRow.name == creator,
                        ),
                    ).scalar_one_or_none()
                s.execute(
                    update(link)
                    .where(link.id == link_id)
                    .values(creator_id=creator_id),
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
        cutoff = datetime.now(UTC) - timedelta(seconds=create_ttl_s)
        link = ShortLinkRow
        with session_scope() as s:
            rows = list(
                s.execute(
                    select(link).where(
                        link.user_id == user_id,
                        link.provider == provider,
                    ),
                ).scalars(),
            )
            by_remote = {r.remote_id: r for r in rows if r.remote_id}
            by_address = {(r.domain, r.slug): r for r in rows}
            seen: set[int] = set()
            kept: list[tuple[ShortLinkRow, list[dict]]] = []
            for remote in remotes:
                row = by_remote.get(remote["remote_id"]) or by_address.get(
                    (remote["domain"], remote["slug"]),
                )
                if row is None:
                    account_id = accounts.get(remote["slug"])
                    made = remote["created_at"]
                    row = ShortLinkRow(
                        user_id=user_id,
                        provider=provider,
                        slug=remote["slug"],
                        domain=remote["domain"],
                        destination=remote["destination"],
                        target=remote["target"],
                        account_id=account_id,
                        creator_id=_creator_of_account(s, account_id),
                        **(
                            {"created_at": datetime.fromtimestamp(made, UTC)}
                            if made
                            else {}
                        ),
                    )
                    s.add(row)
                elif row.status == "deleting":
                    seen.add(row.id)
                    if row.updated_at >= cutoff:
                        continue
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
                kept.append((row, days.get(remote["remote_id"], [])))
            s.flush()
            seen.update(row.id for row, _ in kept)
            self._replace_days(s, {row.id: link_days for row, link_days in kept})
            for row in rows:
                if row.id in seen:
                    continue
                if row.status in (*LIVE, "deleting"):
                    s.delete(row)
                elif row.status == "creating" and row.created_at < cutoff:
                    row.status = "failed"
                    row.error = (
                        "Interrupted while being made: remove it and generate again"
                    )

    @staticmethod
    def _replace_days(s: Session, days_of: dict[int, list[dict]]) -> None:
        days_of = {link_id: days for link_id, days in days_of.items() if days}
        if not days_of:
            return
        s.execute(
            delete(ShortLinkDayRow).where(
                or_(
                    *(
                        and_(
                            ShortLinkDayRow.link_id == link_id,
                            ShortLinkDayRow.day
                            >= min(date.fromisoformat(d["day"]) for d in days),
                        )
                        for link_id, days in days_of.items()
                    ),
                ),
            ),
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
                for link_id, days in days_of.items()
                for d in days
            ],
        )

    @cached
    def syncs(self, owner: int | None = None) -> list[LinkSync]:
        with session_scope() as s:
            rows = s.execute(
                select(LinkSyncRow).where(*mine(LinkSyncRow.user_id, owner)),
            ).scalars()
            return [
                LinkSync.model_validate(
                    {
                        "user_id": r.user_id,
                        "provider": r.provider,
                        "synced_at": r.synced_at,
                        "claimed_at": r.claimed_at,
                        "error": r.error,
                        "usage": r.usage or None,
                    },
                )
                for r in rows
            ]

    @invalidates_cache
    def claim_due(
        self,
        pairs: list[LinkSyncKey],
        interval_s: float,
        claim_ttl_s: float,
    ) -> list[LinkSyncKey]:
        if not pairs:
            return []
        now = time.time()
        sync = LinkSyncRow
        with session_scope() as s:
            s.execute(
                insert(sync)
                .values([{"user_id": k.user_id, "provider": k.provider} for k in pairs])
                .on_conflict_do_nothing(),
            )
            claimed = s.execute(
                update(sync)
                .where(
                    or_(
                        *(
                            (sync.user_id == k.user_id) & (sync.provider == k.provider)
                            for k in pairs
                        ),
                    ),
                    or_(sync.synced_at.is_(None), sync.synced_at < now - interval_s),
                    or_(sync.claimed_at.is_(None), sync.claimed_at < now - claim_ttl_s),
                )
                .values(claimed_at=now)
                .returning(sync.user_id, sync.provider),
            ).all()
            return [
                LinkSyncKey(user_id=u, provider=p)
                for u, p in sorted((u, p) for u, p in claimed)
            ]

    @invalidates_cache
    def mark_due(self, user_id: int, provider: str | None = None) -> None:
        sync = LinkSyncRow
        with session_scope() as s:
            s.execute(
                update(sync)
                .where(
                    sync.user_id == user_id,
                    *([sync.provider == provider] if provider else []),
                )
                .values(synced_at=None, claimed_at=None),
            )

    @invalidates_cache
    def finish_sync(
        self,
        user_id: int,
        provider: str,
        usage: dict[str, Any] | None,
        error: str | None,
    ) -> None:
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
                    index_elements=["user_id", "provider"],
                    set_=values,
                ),
            )
