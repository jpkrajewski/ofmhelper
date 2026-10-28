"""The account tracker sweep's optional daily window (`daily_sweep_due`):
once a day, not before `rescan_daily_at` local time plus a per-day random
jitter -- a fixed schedule is what gets a login flagged as automation."""

from datetime import UTC, datetime

import pytest

from ofmhelpers.web import account_tracker

# tz-aware values; the function only compares wall-clock fields, and the
# tz keeps ruff's DTZ rules happy.
_TZ = UTC


@pytest.fixture
def daily_at_5(monkeypatch):
    monkeypatch.setenv("OFM_ACCOUNTS_RESCAN_DAILY_AT", "05:00")
    monkeypatch.setenv("OFM_ACCOUNTS_RESCAN_DAILY_JITTER_S", "600")


def test_unset_window_is_always_due(monkeypatch):
    monkeypatch.delenv("OFM_ACCOUNTS_RESCAN_DAILY_AT", raising=False)
    assert account_tracker.daily_sweep_due(datetime(2026, 10, 29, 2, 0, tzinfo=_TZ))


def test_before_the_daily_time_is_never_due(daily_at_5):
    # The jitter never makes the window earlier than 05:00.
    assert not account_tracker.daily_sweep_due(
        datetime(2026, 10, 29, 4, 59, tzinfo=_TZ)
    )


def test_past_the_max_jitter_is_always_due(daily_at_5):
    # The jitter never pushes the window past 05:10.
    assert account_tracker.daily_sweep_due(datetime(2026, 10, 29, 5, 11, tzinfo=_TZ))


def test_jitter_is_stable_within_a_day(daily_at_5):
    now = datetime(2026, 10, 29, 5, 3, tzinfo=_TZ)
    assert account_tracker.daily_sweep_due(now) == account_tracker.daily_sweep_due(now)


def test_jitter_shifts_the_window_by_day(daily_at_5):
    # The same clock time lands differently across days: the run time is
    # 05:00 + that day's jitter, so at 05:03 some days are inside the
    # window, some are not.
    outcomes = {
        account_tracker.daily_sweep_due(datetime(2026, 10, day, 5, 3, tzinfo=_TZ))
        for day in range(1, 29)
    }
    assert outcomes == {True, False}


def test_daily_at_is_normalized_and_validated(monkeypatch):
    monkeypatch.delenv("OFM_ACCOUNTS_RESCAN_DAILY_AT", raising=False)
    fresh = account_tracker.settings.accounts.__class__(
        OFM_ACCOUNTS_RESCAN_DAILY_AT="5:3"
    )
    assert fresh.rescan_daily_at == "05:03"
    with pytest.raises(ValueError, match="HH:MM"):
        account_tracker.settings.accounts.__class__(
            OFM_ACCOUNTS_RESCAN_DAILY_AT="25:00"
        )
    with pytest.raises(ValueError, match="HH:MM"):
        account_tracker.settings.accounts.__class__(OFM_ACCOUNTS_RESCAN_DAILY_AT="five")
