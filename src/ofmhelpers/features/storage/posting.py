import datetime

from ofmhelpers.config import settings
from ofmhelpers.features.storage.constants import storage_constants
from ofmhelpers.features.storage.schema import PostingWindow


def posting_day_start(at: datetime.datetime) -> datetime.datetime:
    s = settings.gdrive
    day = storage_constants().day
    posting = datetime.datetime.combine(
        at.astimezone(datetime.UTC).date(), s.posting_time_utc, datetime.UTC
    )
    start = posting - datetime.timedelta(hours=s.posting_window_before_h)
    while start > at:
        start -= day
    while start + day <= at:
        start += day
    return start


def window(at: datetime.datetime | None = None) -> PostingWindow:
    at = at or datetime.datetime.now(datetime.UTC)
    s = settings.gdrive
    length = datetime.timedelta(
        hours=s.posting_window_before_h + s.posting_window_after_h
    )
    start = posting_day_start(at)
    if at >= start + length:
        start += storage_constants().day
    return PostingWindow(
        start=start.timestamp(),
        end=(start + length).timestamp(),
        open=start <= at < start + length,
    )
