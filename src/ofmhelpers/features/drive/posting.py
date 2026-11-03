"""
ofmhelpers/web/posting.py

The team's daily posting window (2026-10-22): `settings.gdrive`
`posting_time_utc` (19:45), from `posting_window_before_h` (1 h) before to
`posting_window_after_h` (5 h) after -- 18:45 to 00:45 UTC, every day. All
in UTC: no daylight saving change, server zone or browser zone moves it.

A posting day starts at the window's start: /drive's "Posted today" counts
from it (drive_folders.py). While the window is open every page shows
"Start posting content" (base.html; static/js/posting.js flips it at the
window's edges on a page left open).
"""

from __future__ import annotations

import datetime

from pydantic import BaseModel

from ofmhelpers.config import settings

_DAY = datetime.timedelta(days=1)


class PostingWindow(BaseModel):
    """Today's window (the one open now, else the next), as Unix seconds."""

    start: float
    end: float
    open: bool


def posting_day_start(at: datetime.datetime) -> datetime.datetime:
    """When the posting day `at` is in began: the latest window start at or
    before `at` (an aware datetime), in UTC."""
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
    """The window open at `at` (default now), else the next one."""
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
