"""The account tracker's fixed scan times (`OFM_ACCOUNTS_SCAN_TIMES`, owner
2026-11-06: 13:50 Warsaw, 10 min before the 14:00 Telegram report, and 18:00): a
scan may be as old as the last scan time that came, so one missed (the app down)
runs on the next pass."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from ofmhelpers.features.accounts import tracker as account_tracker

_WARSAW = ZoneInfo("Europe/Warsaw")


def _at(hour: int, minute: int, day: int = 6) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=_WARSAW)


@pytest.fixture
def clock(monkeypatch):
    """The tracker's clock, set by the test."""
    now: list[datetime] = []

    class _Now(datetime):
        @classmethod
        def now(cls, tz=None):
            return now[0].astimezone(tz)

    monkeypatch.setattr(account_tracker, "datetime", _Now)
    return now


@pytest.mark.parametrize(
    ("now", "age_s"),
    [
        (_at(14, 0), 600),
        (_at(18, 5), 300),
        (_at(3, 0), 9 * 3600),
        (_at(13, 49), 19 * 3600 + 49 * 60),
    ],
    ids=[
        "after 13:50",
        "after 18:00",
        "night: since 18:00",
        "before 13:50: since 18:00",
    ],
)
def test_a_scan_may_be_as_old_as_the_last_scan_time_that_came(clock, now, age_s):
    clock.append(now)

    assert account_tracker._rescan_interval_s() == pytest.approx(age_s)


def test_summer_time_keeps_the_times_on_warsaws_clock(clock):
    clock.append(datetime(2026, 7, 1, 14, 0, tzinfo=_WARSAW))

    assert account_tracker._rescan_interval_s() == pytest.approx(600)


def test_no_scan_times_is_the_interval(clock, monkeypatch):
    monkeypatch.setenv("OFM_ACCOUNTS_SCAN_TIMES", "")
    clock.append(_at(14, 0))

    assert account_tracker._rescan_interval_s() == 6 * 3600


def test_scan_times_are_normalized_and_validated():
    groups = account_tracker.settings.accounts.__class__

    assert groups(OFM_ACCOUNTS_SCAN_TIMES="5:3, 13:50").scan_times == "05:03,13:50"
    for wrong in ("25:00", "five"):
        with pytest.raises(ValueError, match="HH:MM"):
            groups(OFM_ACCOUNTS_SCAN_TIMES=wrong)
