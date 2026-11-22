from functools import lru_cache
from types import MappingProxyType
lazy from datetime import date

lazy from sqlalchemy.orm import InstrumentedAttribute

from ofmhelpers.db.models import SelfLinkClickRow
from ofmhelpers.db.self_links.repository import SelfLinkRepository
from ofmhelpers.db.short_links.catalog import LinkDays, LinkHours
from ofmhelpers.db.short_links.repository import ShortLinkRepository
from ofmhelpers.db.short_links.schema import SelfChart, SelfLinkStat, SelfStats
lazy from ofmhelpers.db.short_links.catalog import RemoteLink
lazy from ofmhelpers.db.short_links.models import SelfLinkDays
lazy from ofmhelpers.db.short_links.schema import (
    BridgeTarget,
    SelfBreakdownRow,
    SelfClick,
    SelfDomain,
    SelfRange,
    SelfTotals,
)

_LIVE = ("active", "inactive")
_COUNTED = ("active", "inactive", "creating")
PROVIDER = "self"


@lru_cache(maxsize=1)
def _self() -> SelfLinkRepository:
    return SelfLinkRepository()


@lru_cache(maxsize=1)
def _links() -> ShortLinkRepository:
    return ShortLinkRepository()


def live_links(user_id: int) -> list[RemoteLink]:
    return [
        row.as_remote() for row in _links().provider_links(user_id, PROVIDER, _LIVE)
    ]


def _remote_to_id(user_id: int) -> MappingProxyType[str, int]:
    return MappingProxyType(
        {
            row.remote_id: row.id
            for row in _links().provider_links(user_id, PROVIDER, _LIVE)
            if row.remote_id
        }
    )


def daily(
    user_id: int, remote_ids: list[str], start: date, end: date
) -> list[LinkDays]:
    ids = _remote_to_id(user_id)
    wanted = {ids[r]: r for r in remote_ids if r in ids}
    return [
        LinkDays(remote_id=wanted[found.link_id], days=found.days)
        for found in _self().daily(list(wanted), start, end)
    ]


def hourly(user_id: int, remote_ids: list[str], day: date) -> list[LinkHours]:
    ids = _remote_to_id(user_id)
    wanted = {ids[r]: r for r in remote_ids if r in ids}
    return [
        LinkHours(remote_id=wanted[found.link_id], hours=found.hours)
        for found in _self().hourly(list(wanted), day)
    ]


def live_count(user_id: int) -> int:
    return len(_links().provider_links(user_id, PROVIDER, _COUNTED))


def active_hosts(owner: int | None = None) -> list[str]:
    return _self().active_hosts(owner)


def list_domains(owner: int | None = None) -> list[SelfDomain]:
    return _self().list_domains(owner)


def add_domain(host: str, owner_id: int | None, status: str = "active") -> int:
    return _self().add_domain(host, owner_id, status)


def set_domain_status(domain_id: int, status: str, owner: int | None = None) -> None:
    _self().set_domain_status(domain_id, status, owner)


def remove_domain(domain_id: int, owner: int | None = None) -> None:
    _self().remove_domain(domain_id, owner)


def record_click(click: SelfClick) -> None:
    _self().record_click(click.model_dump())


def bridge_target(host: str, slug: str) -> BridgeTarget | None:
    return _links().active_self_link(host, slug)


def domains(owner: int | None = None) -> list[SelfDomain]:
    return _self().list_domains(owner)


def days_of(link_ids: list[int], start: date, end: date) -> list[SelfLinkDays]:
    return _self().daily(link_ids, start, end)


def totals(link_ids: list[int], start: date, end: date) -> SelfTotals:
    return _self().totals(link_ids, start, end)


def stats(owner: int, window: SelfRange, page: str | None = None) -> SelfStats:
    span = window.resolved()
    start, end = span.start, span.end
    by_hour = window.by_hour()
    rows = _links().provider_links(owner, PROVIDER, _LIVE)
    if page:
        rows = [r for r in rows if r.slug == page]
    ids = [r.id for r in rows]
    totals = _self().totals(ids, start, end)
    per = {c.link_id: c for c in _self().per_link(ids, start, end)}
    series = _self().series(ids, start, end, by_hour=by_hour)

    def rows_of(column: InstrumentedAttribute[str | None]) -> list[SelfBreakdownRow]:
        return _self().breakdown(ids, start, end, column)

    links = sorted(
        (
            SelfLinkStat(
                id=r.id,
                slug=r.slug,
                domain=r.domain,
                url=r.url,
                destination=r.destination,
                views=per[r.id].views if r.id in per else 0,
                clicks=per[r.id].clicks if r.id in per else 0,
            )
            for r in rows
        ),
        key=lambda link: (link.clicks, link.views),
        reverse=True,
    )
    return SelfStats(
        start=start,
        end=end,
        by_hour=by_hour,
        views=totals.views,
        clicks=totals.clicks,
        visitors=totals.visitors,
        series=series,
        chart=SelfChart.build(series, by_hour=by_hour),
        countries=rows_of(SelfLinkClickRow.country),
        devices=rows_of(SelfLinkClickRow.device),
        platforms=rows_of(SelfLinkClickRow.platform),
        referrers=rows_of(SelfLinkClickRow.referrer),
        browsers=rows_of(SelfLinkClickRow.browser),
        links=links,
    )
