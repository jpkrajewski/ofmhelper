"""Self-hosted bridge pages (provider "self"): the app vocabulary over the two
tables that are ours alone (`self_link_domains`, `self_link_clicks`) plus the
reads of our own `short_links` rows a local provider needs.

Kept independent of `stores/short_links` and of `integrations.short_links.providers`
on purpose: the self provider imports this module, and those two import the
provider registry, so depending on them here would close an import cycle.
"""

from functools import lru_cache
lazy from datetime import date

from ofmhelpers.db.repositories import SelfLinkRepository, ShortLinkRepository
from ofmhelpers.integrations.short_links import DayStat, HourStat, RemoteLink
from ofmhelpers.schemas.self_links import (
    SelfBreakdownRow,
    SelfChart,
    SelfDomain,
    SelfLinkStat,
    SelfSeriesPoint,
    SelfStats,
)
lazy from ofmhelpers.schemas.self_links import SelfClick, SelfRange

# Statuses of a self link that exists (is serveable / counts in the plan).
_LIVE = ("active", "inactive")
_COUNTED = ("active", "inactive", "creating")
PROVIDER = "self"


@lru_cache(maxsize=1)
def _self() -> SelfLinkRepository:
    return SelfLinkRepository()


@lru_cache(maxsize=1)
def _links() -> ShortLinkRepository:
    return ShortLinkRepository()


# --- the self provider's reads ----------------------------------------------


def live_links(user_id: int) -> list[RemoteLink]:
    """`user_id`'s self links that exist, as RemoteLinks (the provider lists
    its own rows)."""
    return [
        RemoteLink(
            remote_id=row["remote_id"],
            slug=row["slug"],
            domain=row["domain"],
            url=row["url"],
            destination=row["destination"],
            active=row["active"],
            adult=row["adult"],
            created_at=row["created_at"],
        )
        for row in _links().provider_links(user_id, PROVIDER, _LIVE)
    ]


def _remote_to_id(user_id: int) -> dict[str, int]:
    return {
        row["remote_id"]: row["id"]
        for row in _links().provider_links(user_id, PROVIDER, _LIVE)
        if row["remote_id"]
    }


def daily(
    user_id: int, remote_ids: list[str], start: date, end: date
) -> dict[str, list[DayStat]]:
    ids = _remote_to_id(user_id)
    wanted = {ids[r]: r for r in remote_ids if r in ids}
    by_link = _self().daily(list(wanted), start, end)
    return {
        wanted[link_id]: [DayStat.model_validate(d) for d in days]
        for link_id, days in by_link.items()
    }


def hourly(user_id: int, remote_ids: list[str], day: date) -> dict[str, list[HourStat]]:
    ids = _remote_to_id(user_id)
    wanted = {ids[r]: r for r in remote_ids if r in ids}
    by_link = _self().hourly(list(wanted), day)
    return {
        wanted[link_id]: [HourStat.model_validate(h) for h in hours]
        for link_id, hours in by_link.items()
    }


def live_count(user_id: int) -> int:
    """How many self links take a place in the plan (live or on their way)."""
    return len(_links().provider_links(user_id, PROVIDER, _COUNTED))


# --- domains ----------------------------------------------------------------


def active_hosts(owner: int | None = None) -> list[str]:
    return _self().active_hosts(owner)


def list_domains(owner: int | None = None) -> list[dict]:
    return _self().list_domains(owner)


def add_domain(host: str, owner_id: int | None, status: str = "active") -> int:
    return _self().add_domain(host, owner_id, status)


def set_domain_status(domain_id: int, status: str, owner: int | None = None) -> None:
    _self().set_domain_status(domain_id, status, owner)


def remove_domain(domain_id: int, owner: int | None = None) -> None:
    _self().remove_domain(domain_id, owner)


# --- clicks -----------------------------------------------------------------


def record_click(click: SelfClick) -> None:
    _self().record_click(click.model_dump())


# --- the bridge -------------------------------------------------------------


def bridge_target(host: str, slug: str) -> dict | None:
    """The live self link served at `host`/`slug` (id + destination), or None.
    What the public bridge page looks up on every hit."""
    return _links().active_self_link(host, slug)


# --- the Self links page ----------------------------------------------------


def domains(owner: int | None = None) -> list[SelfDomain]:
    return [SelfDomain.model_validate(d) for d in _self().list_domains(owner)]


def stats(owner: int, window: SelfRange, page: str | None = None) -> SelfStats:
    """Everything the Self links page draws for `owner` over `window`, read
    from the click log so any time frame works. `page` scopes it to one link
    by its slug (the "All pages" picker); None is all of them."""
    start, end = window.resolved()
    by_hour = window.by_hour()
    rows = _links().provider_links(owner, PROVIDER, _LIVE)
    if page:
        rows = [r for r in rows if r["slug"] == page]
    ids = [r["id"] for r in rows]
    totals = _self().totals(ids, start, end)
    per = _self().per_link(ids, start, end)
    series = [
        SelfSeriesPoint(**p) for p in _self().series(ids, start, end, by_hour=by_hour)
    ]

    def rows_of(column: str) -> list[SelfBreakdownRow]:
        return [
            SelfBreakdownRow(**r) for r in _self().breakdown(ids, start, end, column)
        ]

    links = sorted(
        (
            SelfLinkStat(
                id=r["id"],
                slug=r["slug"],
                domain=r["domain"],
                url=r["url"],
                destination=r["destination"],
                **per.get(r["id"], {"views": 0, "clicks": 0}),
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
        views=totals["views"],
        clicks=totals["clicks"],
        visitors=totals["visitors"],
        series=series,
        chart=SelfChart.build(series, by_hour=by_hour),
        countries=rows_of("country"),
        devices=rows_of("device"),
        platforms=rows_of("platform"),
        referrers=rows_of("referrer"),
        browsers=rows_of("browser"),
        links=links,
    )
