import datetime

from pydantic import BaseModel

from ofmhelpers.config import settings

_DAY = datetime.timedelta(days=1)


class PostingWindow(BaseModel):
    start: float
    end: float
    open: bool


def posting_day_start(at: datetime.datetime) -> datetime.datetime:
    s = settings.gdrive
    posting = datetime.datetime.combine(
        at.astimezone(datetime.UTC).date(), s.posting_time_utc, datetime.UTC
    )
    start = posting - datetime.timedelta(hours=s.posting_window_before_h)
    while start > at:
        start -= _DAY
    while start + _DAY <= at:
        start += _DAY
    return start


def window(at: datetime.datetime | None = None) -> PostingWindow:
    at = at or datetime.datetime.now(datetime.UTC)
    s = settings.gdrive
    length = datetime.timedelta(
        hours=s.posting_window_before_h + s.posting_window_after_h
    )
    start = posting_day_start(at)
    if at >= start + length:
        start += _DAY
    return PostingWindow(
        start=start.timestamp(),
        end=(start + length).timestamp(),
        open=start <= at < start + length,
    )
