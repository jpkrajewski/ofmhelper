"""Self-hosted bridge pages (provider "self"): the custom domains we serve
them on and every hit on them. The links themselves are `short_links` rows
with provider "self" (ShortLinkRepository); this module owns only the two
tables that are ours alone -- `self_link_domains` and `self_link_clicks`.

Clicks are kept one row per hit (not one per day like `short_link_daily_stats`)
so any time frame, country, device or referrer reads back exactly. The domain
list is cached (the public bridge checks the host on every hit) and bumped on
every domain write; the click reads are uncached and never bump the cache, so
recording a hit does not invalidate the host list."""

from __future__ import annotations

import time
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import Date, cast, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert

from ofmhelpers.db.models import SelfLinkClickRow, SelfLinkDomainRow
from ofmhelpers.db.repositories.cached_repository import (
    CachedRepository,
    cached,
    invalidates_cache,
)
from ofmhelpers.db.repositories.owner_scope import mine
from ofmhelpers.db.session import session_scope

VIEW = "view"
CLICK = "click"

# A hit counts as a view and, when it is a continue-through, a click too.
_VIEWS = func.count().filter(SelfLinkClickRow.kind == VIEW)
_CLICKS = func.count().filter(SelfLinkClickRow.kind == CLICK)


def _epoch(day: date) -> float:
    return datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp()


class SelfLinkRepository(CachedRepository):
    cache_namespace = "self_link"

    # --- domains ----------------------------------------------------------

    @cached
    def active_hosts(self, owner: int | None = None) -> list[str]:
        """Hostnames the bridge serves for `owner` (their own plus the shared
        ones); `owner` None: every active host, for the bridge's own check."""
        dom = SelfLinkDomainRow
        where = [dom.status == "active"]
        if owner is not None:
            where.append((dom.owner_id == owner) | (dom.owner_id.is_(None)))
        with session_scope() as s:
            return list(s.execute(select(dom.host).where(*where)).scalars())

    @cached
    def list_domains(self, owner: int | None = None) -> list[dict]:
        """Every domain `owner` manages (their own plus the shared ones),
        newest first."""
        dom = SelfLinkDomainRow
        where = [(dom.owner_id == owner) | (dom.owner_id.is_(None))] if owner else []
        with session_scope() as s:
            rows = s.execute(
                select(dom).where(*where).order_by(dom.added_at.desc(), dom.id.desc())
            ).scalars()
            return [
                {
                    "id": r.id,
                    "host": r.host,
                    "owner_id": r.owner_id,
                    "status": r.status,
                    "added_at": r.added_at,
                }
                for r in rows
            ]

    @invalidates_cache
    def add_domain(self, host: str, owner_id: int | None, status: str) -> int:
        with session_scope() as s:
            row = SelfLinkDomainRow(
                host=host.strip().lower(),
                owner_id=owner_id,
                status=status,
                added_at=time.time(),
            )
            s.add(row)
            s.flush()
            return row.id

    @invalidates_cache
    def set_domain_status(
        self, domain_id: int, status: str, owner: int | None = None
    ) -> None:
        dom = SelfLinkDomainRow
        with session_scope() as s:
            s.execute(
                update(dom)
                .where(dom.id == domain_id, *mine(dom.owner_id, owner))
                .values(status=status)
            )

    @invalidates_cache
    def remove_domain(self, domain_id: int, owner: int | None = None) -> None:
        dom = SelfLinkDomainRow
        with session_scope() as s:
            s.execute(
                delete(dom).where(dom.id == domain_id, *mine(dom.owner_id, owner))
            )

    # --- clicks -----------------------------------------------------------

    def record_click(self, click: dict[str, Any]) -> None:
        """One hit on a bridge page (`kind` view or click)."""
        with session_scope() as s:
            s.execute(insert(SelfLinkClickRow).values(ts=time.time(), **click))

    def daily(
        self, link_ids: list[int], start: date, end: date
    ) -> dict[int, list[dict]]:
        """Per link id, each UTC day in [start, end] that had any hit: views
        (landings) and clicks (continue-throughs)."""
        if not link_ids:
            return {}
        click = SelfLinkClickRow
        day = cast(func.to_timestamp(click.ts).op("AT TIME ZONE")("UTC"), Date)
        found: dict[int, list[dict]] = {}
        with session_scope() as s:
            rows = s.execute(
                select(click.link_id, day.label("d"), _VIEWS, _CLICKS)
                .where(
                    click.link_id.in_(link_ids),
                    click.ts >= _epoch(start),
                    click.ts < _epoch(end) + 86400,
                )
                .group_by(click.link_id, day)
                .order_by(day)
            ).all()
        for link_id, d, views, clicks in rows:
            found.setdefault(link_id, []).append(
                {"day": d.isoformat(), "views": int(views), "clicks": int(clicks)}
            )
        return found

    def hourly(self, link_ids: list[int], day: date) -> dict[int, list[dict]]:
        """Per link id, each UTC hour of `day` that had any hit."""
        if not link_ids:
            return {}
        click = SelfLinkClickRow
        hour = func.date_trunc(
            "hour", func.to_timestamp(click.ts).op("AT TIME ZONE")("UTC")
        )
        found: dict[int, list[dict]] = {}
        with session_scope() as s:
            rows = s.execute(
                select(click.link_id, hour.label("h"), _VIEWS, _CLICKS)
                .where(
                    click.link_id.in_(link_ids),
                    click.ts >= _epoch(day),
                    click.ts < _epoch(day) + 86400,
                )
                .group_by(click.link_id, hour)
                .order_by(hour)
            ).all()
        for link_id, h, views, clicks in rows:
            when = h.replace(tzinfo=UTC) if h.tzinfo is None else h
            found.setdefault(link_id, []).append(
                {
                    "hour": when.isoformat(),
                    "views": int(views),
                    "clicks": int(clicks),
                }
            )
        return found

    # --- stats for the Self links page ------------------------------------

    def totals(self, link_ids: list[int], start: date, end: date) -> dict[str, int]:
        """Views, clicks and unique visitors (distinct IP) over [start, end]."""
        if not link_ids:
            return {"views": 0, "clicks": 0, "visitors": 0}
        click = SelfLinkClickRow
        with session_scope() as s:
            row = s.execute(
                select(
                    _VIEWS.label("v"),
                    _CLICKS.label("c"),
                    func.count(func.distinct(click.ip)).label("u"),
                ).where(
                    click.link_id.in_(link_ids),
                    click.ts >= _epoch(start),
                    click.ts < _epoch(end) + 86400,
                )
            ).one()
        return {"views": int(row.v), "clicks": int(row.c), "visitors": int(row.u)}

    def series(
        self, link_ids: list[int], start: date, end: date, *, by_hour: bool
    ) -> list[dict]:
        """Views and clicks per bucket (UTC hour when `by_hour`, else UTC day)
        summed across the links, oldest first."""
        if not link_ids:
            return []
        click = SelfLinkClickRow
        stamp = func.to_timestamp(click.ts).op("AT TIME ZONE")("UTC")
        bucket = func.date_trunc("hour", stamp) if by_hour else cast(stamp, Date)
        with session_scope() as s:
            rows = s.execute(
                select(bucket.label("b"), _VIEWS, _CLICKS)
                .where(
                    click.link_id.in_(link_ids),
                    click.ts >= _epoch(start),
                    click.ts < _epoch(end) + 86400,
                )
                .group_by(bucket)
                .order_by(bucket)
            ).all()
        return [
            {"bucket": b.isoformat(), "views": int(views), "clicks": int(clicks)}
            for b, views, clicks in rows
        ]

    def breakdown(
        self, link_ids: list[int], start: date, end: date, column: str, limit: int = 8
    ) -> list[dict]:
        """The top `column` values by clicks (continue-throughs) over the
        range: [{value, clicks}], most first. `column` is one of country,
        region, city, platform, device, referrer."""
        if not link_ids:
            return []
        click = SelfLinkClickRow
        col = getattr(click, column)
        with session_scope() as s:
            rows = s.execute(
                select(col.label("value"), func.count().label("clicks"))
                .where(
                    click.link_id.in_(link_ids),
                    click.kind == CLICK,
                    click.ts >= _epoch(start),
                    click.ts < _epoch(end) + 86400,
                    col.is_not(None),
                )
                .group_by(col)
                .order_by(func.count().desc())
                .limit(limit)
            ).all()
        return [{"value": value, "clicks": int(clicks)} for value, clicks in rows]

    def per_link(
        self, link_ids: list[int], start: date, end: date
    ) -> dict[int, dict[str, int]]:
        """Views and clicks per link over the range."""
        if not link_ids:
            return {}
        click = SelfLinkClickRow
        with session_scope() as s:
            rows = s.execute(
                select(click.link_id, _VIEWS, _CLICKS)
                .where(
                    click.link_id.in_(link_ids),
                    click.ts >= _epoch(start),
                    click.ts < _epoch(end) + 86400,
                )
                .group_by(click.link_id)
            ).all()
        return {
            link_id: {"views": int(views), "clicks": int(clicks)}
            for link_id, views, clicks in rows
        }
