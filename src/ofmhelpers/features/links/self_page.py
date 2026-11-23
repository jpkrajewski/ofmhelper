# Copyright (C) 2026 jpkrajewski

from datetime import date, timedelta

from pydantic import BaseModel

from ofmhelpers.db.self_links import store as self_links
from ofmhelpers.db.short_links.schema import (
    STAT_PERIODS,
    SelfDomain,
    SelfRange,
    SelfStats,
    utc_today,
)
from ofmhelpers.features.refusals import NotFoundError, RefusedError, duplicate_refused
from ofmhelpers.integrations.short_links import RemoteLink

_STATUSES = ("active", "disabled")


class PageView(BaseModel):
    stats: SelfStats
    domains: list[SelfDomain]
    pages: list[RemoteLink]
    page: str | None
    periods: tuple[int, ...]
    yesterday: date


def page_view(query: SelfRange, scope: int) -> PageView:
    return PageView(
        stats=self_links.stats(scope, query, page=query.page),
        domains=self_links.domains(scope),
        pages=self_links.live_links(scope),
        page=query.page,
        periods=STAT_PERIODS,
        yesterday=utc_today() - timedelta(days=1),
    )


def add_domain(host: str, owner_id: int) -> None:
    if host in {d.host for d in self_links.domains(owner_id)}:
        msg = f"{host} is already added"
        raise RefusedError(msg)
    with duplicate_refused():
        self_links.add_domain(host, owner_id, status="active")


def _own_domain(domain_id: int, owner: int) -> None:
    if domain_id not in {d.id for d in self_links.domains(owner)}:
        msg = "Domain not found"
        raise NotFoundError(msg)


def set_status(domain_id: int, status: str, owner_id: int) -> None:
    if status not in _STATUSES:
        msg = "Unknown status"
        raise RefusedError(msg)
    _own_domain(domain_id, owner_id)
    self_links.set_domain_status(domain_id, status, owner_id)


def delete_domain(domain_id: int, owner_id: int) -> None:
    _own_domain(domain_id, owner_id)
    self_links.remove_domain(domain_id, owner_id)
