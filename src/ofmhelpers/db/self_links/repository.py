import time
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import Date, cast, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
lazy from sqlalchemy.orm import InstrumentedAttribute

from ofmhelpers.db.cached_repository import CachedRepository, cached, invalidates_cache
from ofmhelpers.db.devices.repository import DuplicateError
from ofmhelpers.db.models import SelfLinkClickRow, SelfLinkDomainRow, ShortLinkRow
from ofmhelpers.db.owner_scope import mine
from ofmhelpers.db.session import session_scope
from ofmhelpers.db.short_links.catalog import DayStat, HourStat
from ofmhelpers.db.short_links.models import SelfLinkDays, SelfLinkHours
from ofmhelpers.db.short_links.schema import (
    SelfBreakdownRow,
    SelfDomain,
    SelfLinkCount,
    SelfSeriesPoint,
    SelfTotals,
)

VIEW = "view"
CLICK = "click"

_VIEWS = func.count().filter(SelfLinkClickRow.kind == VIEW)
_CLICKS = func.count().filter(SelfLinkClickRow.kind == CLICK)


def _epoch(day: date) -> float:
    return datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp()


class SelfLinkRepository(CachedRepository):
    cache_namespace = "self_link"

    @cached
    def active_hosts(self, owner: int | None = None) -> list[str]:
        dom = SelfLinkDomainRow
        where = [dom.status == "active"]
        if owner is not None:
            where.append((dom.owner_id == owner) | (dom.owner_id.is_(None)))
        with session_scope() as s:
            return list(s.execute(select(dom.host).where(*where)).scalars())

    @cached
    def list_domains(self, owner: int | None = None) -> list[SelfDomain]:
        dom = SelfLinkDomainRow
        where = [(dom.owner_id == owner) | (dom.owner_id.is_(None))] if owner else []
        with session_scope() as s:
            rows = s.execute(
                select(dom).where(*where).order_by(dom.created_at.desc(), dom.id.desc())
            ).scalars()
            return [
                SelfDomain(
                    id=r.id,
                    host=r.host,
                    owner_id=r.owner_id,
                    status=r.status,
                    added_at=r.created_at.timestamp(),
                )
                for r in rows
            ]

    @invalidates_cache
    def add_domain(self, host: str, owner_id: int | None, status: str) -> int:
        with session_scope() as s:
            row = SelfLinkDomainRow(
                host=host.strip().lower(),
                owner_id=owner_id,
                status=status,
            )
            s.add(row)
            try:
                s.flush()
            except IntegrityError as exc:
                msg = "This domain can't be added"
                raise DuplicateError(msg) from exc
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
            gone = s.execute(
                select(dom.host, dom.owner_id).where(
                    dom.id == domain_id, *mine(dom.owner_id, owner)
                )
            ).first()
            if gone is not None and gone.owner_id is not None:
                s.execute(
                    update(ShortLinkRow)
                    .where(
                        ShortLinkRow.provider == "self",
                        ShortLinkRow.domain == gone.host,
                        ShortLinkRow.user_id == gone.owner_id,
                        ShortLinkRow.status == "active",
                    )
                    .values(status="inactive")
                )
            s.execute(
                delete(dom).where(dom.id == domain_id, *mine(dom.owner_id, owner))
            )

    def record_click(self, click: dict[str, Any]) -> None:
        with session_scope() as s:
            s.execute(insert(SelfLinkClickRow).values(ts=time.time(), **click))

    def daily(self, link_ids: list[int], start: date, end: date) -> list[SelfLinkDays]:
        if not link_ids:
            return []
        click = SelfLinkClickRow
        day = cast(func.to_timestamp(click.ts).op("AT TIME ZONE")("UTC"), Date)
        found: dict[int, SelfLinkDays] = {}
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
            found.setdefault(
                link_id, SelfLinkDays(link_id=link_id, days=[])
            ).days.append(DayStat(day=d, views=int(views), clicks=int(clicks)))
        return list(found.values())

    def hourly(self, link_ids: list[int], day: date) -> list[SelfLinkHours]:
        if not link_ids:
            return []
        click = SelfLinkClickRow
        hour = func.date_trunc(
            "hour", func.to_timestamp(click.ts).op("AT TIME ZONE")("UTC")
        )
        found: dict[int, SelfLinkHours] = {}
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
            found.setdefault(
                link_id, SelfLinkHours(link_id=link_id, hours=[])
            ).hours.append(HourStat(hour=when, views=int(views), clicks=int(clicks)))
        return list(found.values())

    def totals(self, link_ids: list[int], start: date, end: date) -> SelfTotals:
        if not link_ids:
            return SelfTotals()
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
        return SelfTotals(views=int(row.v), clicks=int(row.c), visitors=int(row.u))

    def series(
        self, link_ids: list[int], start: date, end: date, *, by_hour: bool
    ) -> list[SelfSeriesPoint]:
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
            SelfSeriesPoint(bucket=b.isoformat(), views=int(views), clicks=int(clicks))
            for b, views, clicks in rows
        ]

    def breakdown(
        self,
        link_ids: list[int],
        start: date,
        end: date,
        col: InstrumentedAttribute[str | None],
        limit: int = 8,
    ) -> list[SelfBreakdownRow]:
        if not link_ids:
            return []
        click = SelfLinkClickRow
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
        return [
            SelfBreakdownRow(value=value, clicks=int(clicks)) for value, clicks in rows
        ]

    def per_link(
        self, link_ids: list[int], start: date, end: date
    ) -> list[SelfLinkCount]:
        if not link_ids:
            return []
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
        return [
            SelfLinkCount(link_id=link_id, views=int(views), clicks=int(clicks))
            for link_id, views, clicks in rows
        ]
