"""Clicks and landing views since the previous report, from counters that exist
only per UTC day (`short_link_daily_stats`, rewritten by every sync).

A run saves each link's days from the day before it ran (`ReportMarks`); the next
counts, per link, what each of those days and the newer ones gained since. That is
exactly what came in between the two runs (as fresh as the links' last sync,
which the report runs first)."""

from datetime import UTC, date, datetime, timedelta

from ofmhelpers.schemas.reports import DayCount, ReportMarks
lazy from ofmhelpers.schemas.links import ShortLink


def marks(links: list[ShortLink], now: float) -> ReportMarks:
    """What a run saves: each link's days from yesterday (UTC)."""
    since = _utc_day(now) - timedelta(days=1)
    return ReportMarks(
        links={
            link.id: {
                d.day: DayCount(views=d.views, clicks=d.clicks)
                for d in link.days
                if d.day >= since
            }
            for link in links
        }
    )


def gained(link: ShortLink, previous: ReportMarks, since: float) -> DayCount:
    """What `link` gained since the run that saved `previous` at `since`."""
    seen = previous.links.get(link.id)
    # Marked: from the day before the run (its clicks still coming in then); new
    # since: only from the run's day, the days before it are not in the window.
    first = _utc_day(since) - timedelta(days=1) if seen is not None else _utc_day(since)
    seen = seen or {}
    views = clicks = 0
    for day in link.days:
        if day.day < first:
            continue
        before = seen.get(day.day, DayCount())
        views += max(day.views - before.views, 0)
        clicks += max(day.clicks - before.clicks, 0)
    return DayCount(views=views, clicks=clicks)


def _utc_day(at: float) -> date:
    return datetime.fromtimestamp(at, UTC).date()
