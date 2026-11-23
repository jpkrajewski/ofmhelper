# Copyright (C) 2026 jpkrajewski

import datetime as dt

from ofmhelpers.config import settings
from ofmhelpers.features.storage.constants import posting_constants
from ofmhelpers.features.storage.schema import PostingWindow


def posting_day_start(at: dt.datetime) -> dt.datetime:
    s = settings.gdrive
    day = posting_constants().day
    posting = dt.datetime.combine(
        at.astimezone(dt.UTC).date(),
        s.posting_time_utc,
        dt.UTC,
    )
    start = posting - dt.timedelta(hours=s.posting_window_before_h)
    while start > at:
        start -= day
    while start + day <= at:
        start += day
    return start


def window(at: dt.datetime | None = None) -> PostingWindow:
    at = at or dt.datetime.now(dt.UTC)
    s = settings.gdrive
    length = dt.timedelta(
        hours=s.posting_window_before_h + s.posting_window_after_h,
    )
    start = posting_day_start(at)
    if at >= start + length:
        start += posting_constants().day
    return PostingWindow(
        start=start.timestamp(),
        end=(start + length).timestamp(),
        open=start <= at < start + length,
    )
