# Copyright (C) 2026 jpkrajewski

import logging
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel

from ofmhelpers.db.report_runs.schema import DayCount
from ofmhelpers.features.links import keys as link_keys
from ofmhelpers.integrations.short_links.errors import LinkError
from ofmhelpers.integrations.short_links.providers import get_link_provider
lazy from ofmhelpers.db.short_links.models import ShortLink

logger = logging.getLogger(__name__)

_HOURS = 24


class LastDay(BaseModel):
    counts: dict[int, DayCount] = {}
    unread: list[str] = []


def window_start(now: float) -> datetime:
    current = datetime.fromtimestamp(now, UTC).replace(
        minute=0,
        second=0,
        microsecond=0,
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
        api_key = link_keys.api_key(owner, key)
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
                for one in per_link
                if one.remote_id == link.remote_id
                for hour in one.hours
                if hour.hour >= start
            ]
            found.counts[link.id] = DayCount(
                views=sum(h.views for h in hours),
                clicks=sum(h.clicks for h in hours),
            )
    return found
