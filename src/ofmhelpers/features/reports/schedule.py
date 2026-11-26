# Copyright (C) 2026 jpkrajewski

from datetime import date, datetime, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from ofmhelpers.config import settings
lazy from ofmhelpers.db.report_runs.schema import ReportRun


class Due(StrEnum):
    NOTHING = "nothing"
    BASELINE = "baseline"
    SEND = "send"


def local(now: float) -> datetime:
    return datetime.fromtimestamp(now, ZoneInfo(settings.reports.timezone))


def _past_time(at: datetime) -> bool:
    return at.time() >= settings.reports.daily_time()


class Planned(BaseModel, frozen=True):
    action: Due
    day: date


def due(now: float, last: ReportRun | None) -> Planned:
    at = local(now)
    today = at.date()
    if last is None:
        day = today if _past_time(at) else today - timedelta(days=1)
        return Planned(action=Due.BASELINE, day=day)
    if last.day >= today or not _past_time(at):
        return Planned(action=Due.NOTHING, day=today)
    return Planned(action=Due.SEND, day=today)
