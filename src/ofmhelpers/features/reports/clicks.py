from datetime import UTC, date, datetime, timedelta

from ofmhelpers.db.report_runs.schema import DayCount, ReportMarks
lazy from ofmhelpers.db.short_links.schema import ShortLink


def marks(links: list[ShortLink], now: float) -> ReportMarks:
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
    seen = previous.links.get(link.id)
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
