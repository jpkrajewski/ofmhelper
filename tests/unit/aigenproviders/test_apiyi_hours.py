"""When the provider select warns that APIYI can be slow (apiyi/hours.py):
China's evening, 11:00-17:00 UTC by default, with the hours it is fastest."""

from datetime import UTC, datetime

import pytest

from ofmhelpers.integrations.ai_providers.apiyi.hours import slow_notice

SLOW = "APIYI can be slow right now. Fastest from {17:00Z} to {11:00Z}, {zone} time"


def _at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, 12, hour, minute, tzinfo=UTC)


# Prod 2026-11-12: both failed clones were sent at 15:18 UTC.
@pytest.mark.parametrize("now", [_at(11), _at(15, 18), _at(16, 59)])
def test_inside_the_slow_hours_it_names_the_fastest_hours(now):
    assert slow_notice(now) == SLOW


@pytest.mark.parametrize("now", [_at(10, 59), _at(17), _at(22), _at(3)])
def test_outside_the_slow_hours_it_says_nothing(now):
    assert slow_notice(now) == ""


def test_hours_that_cross_midnight(monkeypatch):
    monkeypatch.setenv("OFM_APIYI_SLOW_FROM_UTC_H", "22")
    monkeypatch.setenv("OFM_APIYI_SLOW_UNTIL_UTC_H", "2")

    said = "APIYI can be slow right now. Fastest from {02:00Z} to {22:00Z}, {zone} time"
    assert slow_notice(_at(23)) == said
    assert slow_notice(_at(1, 30)) == said
    assert slow_notice(_at(2)) == ""
    assert slow_notice(_at(21, 59)) == ""


def test_equal_hours_never_warn(monkeypatch):
    monkeypatch.setenv("OFM_APIYI_SLOW_FROM_UTC_H", "0")
    monkeypatch.setenv("OFM_APIYI_SLOW_UNTIL_UTC_H", "0")

    assert slow_notice(_at(0)) == ""
    assert slow_notice(_at(12)) == ""
