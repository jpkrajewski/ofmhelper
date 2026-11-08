"""Each link's landing views and clicks in the last 24 hours, asked of its
provider at once: what `money` answers (owner 2026-11-08: "current stuff", not
"since the report").

A one-day range comes back by the hour on both providers (UTC; links/CLAUDE.md,
Oopsie checked on its real API 2026-11-08), so the window is the current hour
and the 23 before it, read from at most two days per provider: two Oopsie calls
for up to 200 links, two Bouncy calls per link. Nothing synced or stored.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from pydantic import BaseModel

from ofmhelpers.db.stores import short_links as link_store
from ofmhelpers.integrations.short_links.errors import LinkError
from ofmhelpers.integrations.short_links.providers import get_link_provider
from ofmhelpers.log import get_logger
from ofmhelpers.schemas.reports import DayCount

if TYPE_CHECKING:
    from ofmhelpers.schemas.links import ShortLink

logger = get_logger(__name__)

_HOURS = 24


class LastDay(BaseModel):
    """Per link id, its numbers; and the providers that could not tell now
    (their label: their links' clicks are missing from the counts)."""

    counts: dict[int, DayCount] = {}
    unread: list[str] = []


def window_start(now: float) -> datetime:
    """The first hour counted: 23 before the current one."""
    current = datetime.fromtimestamp(now, UTC).replace(
        minute=0, second=0, microsecond=0
    )
    return current - timedelta(hours=_HOURS - 1)


def last_day(links: list[ShortLink], owner: int, now: float) -> LastDay:
    start = window_start(now)
    days = sorted({start.date(), datetime.fromtimestamp(now, UTC).date()})
    by_provider: dict[str, list[ShortLink]] = {}
    for link in links:
        if link.remote_id:
            by_provider.setdefault(link.provider, []).append(link)
    found = LastDay()
    for key, mine in sorted(by_provider.items()):
        api_key = link_store.api_key(owner, key)
        if not api_key:
            continue
        provider = get_link_provider(key)(api_key)
        ids = [link.remote_id for link in mine if link.remote_id]
        try:
            read = [provider.hourly(ids, day) for day in days]
        except LinkError:
            logger.warning("%s: the last 24 hours not read", key, exc_info=True)
            found.unread.append(provider.label())
            continue
        for link in mine:
            hours = [
                hour
                for per_link in read
                for hour in per_link.get(link.remote_id or "", [])
                if hour.hour >= start
            ]
            found.counts[link.id] = DayCount(
                views=sum(h.views for h in hours), clicks=sum(h.clicks for h in hours)
            )
    return found
