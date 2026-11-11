"""When the daily report goes out: at `OFM_REPORTS_DAILY_AT` on
`OFM_REPORTS_TIMEZONE`'s clock (summer time included), once a day; a day whose
time passed while the app was down goes out when it is back, that day.

Never run before: a baseline at once (the counters only, nothing sent), so the
first report counts from then. Taken before today's time it stands for
yesterday's run, so today's still goes out; taken after, today's."""

from datetime import date, datetime, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from ofmhelpers.config import settings
lazy from ofmhelpers.schemas.reports import ReportRun


class Due(StrEnum):
    NOTHING = "nothing"
    BASELINE = "baseline"
    SEND = "send"


def local(now: float) -> datetime:
    return datetime.fromtimestamp(now, ZoneInfo(settings.reports.timezone))


def _past_time(at: datetime) -> bool:
    return at.time() >= settings.reports.daily_time()


class Planned(BaseModel, frozen=True):
    """What to do now, and the report day it stands for."""

    action: Due
    day: date


def due(now: float, last: ReportRun | None) -> Planned:
    """What to do now, and the report day it stands for."""
    at = local(now)
    today = at.date()
    if last is None:
        day = today if _past_time(at) else today - timedelta(days=1)
        return Planned(action=Due.BASELINE, day=day)
    if last.day >= today or not _past_time(at):
        return Planned(action=Due.NOTHING, day=today)
    return Planned(action=Due.SEND, day=today)
