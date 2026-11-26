# Copyright (C) 2026 jpkrajewski

from datetime import date, datetime
from zoneinfo import ZoneInfo

from ofmhelpers.db.jobs.schema import SessionSpan
from ofmhelpers.db.users.schema import Role, User
from ofmhelpers.features.vas.hours import day_hours, worded

WARSAW = ZoneInfo("Europe/Warsaw")


def _ts(day: int, hour: int, minute: int = 0, month: int = 11) -> float:
    return datetime(2026, month, day, hour, minute, tzinfo=WARSAW).timestamp()


def _va(last_seen_at: float | None = None) -> User:
    return User(
        id=7,
        email="va@example.com",
        role=Role.VA,
        created_at=0,
        last_seen_at=last_seen_at,
    )


def test_worded():
    assert [worded(s) for s in (0, 59, 60, 3600, 27_720)] == [
        "—",
        "—",
        "1 m",
        "1 h 00 m",
        "7 h 42 m",
    ]


def test_a_session_still_open_counts_until_the_last_action():
    span = SessionSpan(user_id=7, start_at=_ts(15, 8))

    (day,) = day_hours([span], [_va(_ts(15, 11, 45))], WARSAW)

    assert (day.day, day.seconds) == (date(2026, 11, 15), 3 * 3600 + 45 * 60)


def test_a_session_with_nothing_after_it_and_no_last_action_counts_nothing():
    span = SessionSpan(user_id=7, start_at=_ts(15, 8))

    assert day_hours([span], [_va()], WARSAW) == []


def test_the_night_the_clocks_go_back_counts_real_hours():
    span = SessionSpan(
        user_id=7,
        start_at=_ts(25, 0, month=10),
        end_at=_ts(25, 4, month=10),
    )

    (day,) = day_hours([span], [_va()], WARSAW)

    assert day.seconds == 5 * 3600
