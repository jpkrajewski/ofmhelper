"""links/: the self provider's shapes: the window the Self links page reads
and one of our own link rows as the provider lists it."""

from datetime import date

import pytest
from pydantic import ValidationError

from ofmhelpers.schemas.self_links import MAX_STAT_DAYS, DateSpan, SelfLink, SelfRange

TODAY = date(2026, 11, 11)


def test_a_days_preset_ends_today():
    span = SelfRange(days=7).resolved(TODAY)
    assert span == DateSpan(start=date(2026, 11, 5), end=TODAY)
    assert SelfRange(days=7).by_hour(TODAY) is False


def test_an_explicit_window_is_ordered_and_kept_within_the_limits():
    window = SelfRange.model_validate({"from": "2026-12-01", "to": "2020-01-01"})
    span = window.resolved(TODAY)
    assert (TODAY - span.start).days == MAX_STAT_DAYS - 1
    assert span.end == TODAY


def test_a_single_day_is_read_by_the_hour():
    window = SelfRange.model_validate({"from": "2026-11-10", "to": "2026-11-10"})
    assert window.resolved(TODAY) == DateSpan(
        start=date(2026, 11, 10), end=date(2026, 11, 10)
    )
    assert window.by_hour(TODAY) is True


def _row(remote_id: str | None) -> SelfLink:
    return SelfLink(
        id=7,
        remote_id=remote_id,
        slug="dany",
        domain="bridge.test",
        url="https://bridge.test/dany",
        destination="https://onlyfans.com/dany",
        active=True,
        adult=True,
        created_at=1.0,
    )


def test_a_made_row_is_a_remote_link_without_its_row_id():
    remote = _row("bridge.test/dany").as_remote()
    assert remote.model_dump() == {
        "remote_id": "bridge.test/dany",
        "slug": "dany",
        "domain": "bridge.test",
        "url": "https://bridge.test/dany",
        "destination": "https://onlyfans.com/dany",
        "active": True,
        "adult": True,
        "created_at": 1.0,
    }


def test_a_row_still_being_made_is_no_remote_link():
    with pytest.raises(ValidationError):
        _row(None).as_remote()
