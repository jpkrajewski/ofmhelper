"""The daily report's parts that need nothing stored: how numbers read, when it is
due on Warsaw's clock (summer time too), and clicks counted between two runs."""

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest

from ofmhelpers.features.reports import clicks, daily
from ofmhelpers.features.reports.fan_sites import FanStats
from ofmhelpers.features.reports.format import compact, money, percent, window_title
from ofmhelpers.features.reports.schedule import Due, due
from ofmhelpers.integrations.short_links import DayStat
from ofmhelpers.schemas.chatting import ChatPlatform
from ofmhelpers.schemas.links import ShortLink
from ofmhelpers.schemas.reports import ReportMarks, ReportRun

_WARSAW = ZoneInfo("Europe/Warsaw")


def _at(*parts: int) -> float:
    return datetime(*parts, tzinfo=_WARSAW).timestamp()


def _run(day: date) -> ReportRun:
    return ReportRun(name="daily", day=day, ran_at=0, sent=True, marks=ReportMarks())


@pytest.mark.parametrize(
    ("n", "text"),
    [
        (None, "—"),
        (0, "0"),
        (512, "512"),
        (5000, "5k"),
        (1340, "1.3k"),
        (1_300_000, "1.3M"),
    ],
)
def test_counts_read_short(n, text):
    assert compact(n) == text


@pytest.mark.parametrize(
    ("cents", "text"),
    [(None, "—"), (0, "$0"), (1250, "$12.50"), (5099, "$50.99"), (543_200, "$5,432")],
)
def test_money_is_dollars(cents, text):
    assert money(cents) == text


@pytest.mark.parametrize(
    ("value", "text"),
    [(None, "—"), (100.0, "100%"), (12.4, "12%"), (2.5, "2.5%"), (1.0, "1%")],
)
def test_percent(value, text):
    assert percent(value) == text


def test_a_day_long_window_is_the_last_24h_and_another_says_where_it_starts():
    since = datetime(2026, 11, 5, 14, 0, tzinfo=_WARSAW)

    assert (
        window_title(since, datetime(2026, 11, 6, 14, 1, tzinfo=_WARSAW)) == "Last 24H"
    )
    assert window_title(since, datetime(2026, 11, 7, 14, 0, tzinfo=_WARSAW)) == (
        "Since 5 Nov 14:00"
    )


def test_the_first_look_takes_a_baseline_that_still_lets_todays_report_out():
    assert due(_at(2026, 11, 6, 10, 0), None) == (Due.BASELINE, date(2026, 11, 5))
    assert due(_at(2026, 11, 6, 15, 0), None) == (Due.BASELINE, date(2026, 11, 6))


def test_it_goes_out_at_14_warsaw_once_a_day():
    yesterday = _run(date(2026, 11, 5))

    assert due(_at(2026, 11, 6, 13, 59), yesterday)[0] == Due.NOTHING
    assert due(_at(2026, 11, 6, 14, 0), yesterday) == (Due.SEND, date(2026, 11, 6))
    assert due(_at(2026, 11, 6, 20, 0), _run(date(2026, 11, 6)))[0] == Due.NOTHING


def test_a_day_missed_while_down_goes_out_when_back():
    assert due(_at(2026, 11, 8, 9, 0), _run(date(2026, 11, 5))) == (
        Due.NOTHING,
        date(2026, 11, 8),
    )
    assert due(_at(2026, 11, 8, 16, 30), _run(date(2026, 11, 5))) == (
        Due.SEND,
        date(2026, 11, 8),
    )


@pytest.mark.parametrize(
    ("utc_hour", "sent"),
    [(11, False), (12, True)],
    ids=["11 UTC is 13 in summer", "12 UTC is 14 in summer"],
)
def test_summer_time_moves_it_an_hour_in_utc(utc_hour, sent):
    now = datetime(2026, 7, 1, utc_hour, 0, tzinfo=UTC).timestamp()

    assert (due(now, _run(date(2026, 6, 30)))[0] == Due.SEND) is sent


def _link(link_id: int, *days: tuple[date, int, int]) -> ShortLink:
    return ShortLink(
        id=link_id,
        user_id=1,
        provider="oopsie",
        slug=f"s{link_id}",
        domain="oop.si",
        destination="https://www.stacked.com/@dane",
        status="active",
        created_at=0,
        days=[DayStat(day=d, views=v, clicks=c) for d, v, c in days],
    )


_RAN = datetime(2026, 11, 5, 13, 0, tzinfo=UTC).timestamp()
_NOV4, _NOV5, _NOV6 = date(2026, 11, 4), date(2026, 11, 5), date(2026, 11, 6)


def test_clicks_count_only_what_came_in_since_the_last_run():
    before = clicks.marks([_link(1, (_NOV4, 500, 400), (_NOV5, 100, 80))], _RAN)
    later = _link(1, (_NOV4, 500, 400), (_NOV5, 300, 250), (_NOV6, 200, 150))

    got = clicks.gained(later, before, _RAN)

    assert (got.views, got.clicks) == (400, 320)


def test_a_link_new_since_the_last_run_counts_all_its_days_since():
    got = clicks.gained(_link(2, (_NOV4, 9, 9), (_NOV5, 30, 20)), ReportMarks(), _RAN)

    assert (got.views, got.clicks) == (30, 20)


def test_a_day_a_provider_rewrote_lower_counts_nothing_not_less():
    before = clicks.marks([_link(1, (_NOV5, 100, 80))], _RAN)

    got = clicks.gained(_link(1, (_NOV5, 90, 70)), before, _RAN)

    assert (got.views, got.clicks) == (0, 0)


def test_late_clicks_on_the_day_before_the_last_run_still_count():
    """Run at 00:30 UTC on Nov 6: Nov 5's clicks were still coming in."""
    ran = datetime(2026, 11, 6, 0, 30, tzinfo=UTC).timestamp()
    before = clicks.marks([_link(1, (_NOV5, 100, 80))], ran)

    got = clicks.gained(_link(1, (_NOV5, 120, 95), (_NOV6, 10, 5)), before, ran)

    assert (got.views, got.clicks) == (30, 20)


class _Site:
    def __init__(self, platform, numbers: dict[str, tuple[int, int]]) -> None:
        self.platform = platform
        self._numbers = numbers

    def models(self) -> list[str]:
        return list(self._numbers)

    def stats(self, username: str):
        subs, cents = self._numbers[username]
        return FanStats(subs=subs, cents=cents)


def _rows(monkeypatch, links, sites) -> dict[str, tuple[int | None, int | None]]:
    monkeypatch.setattr(daily, "fan_sites", lambda *_: {s.platform: s for s in sites})
    previous = ReportRun(
        name="daily", day=_NOV5, ran_at=_RAN, sent=True, marks=ReportMarks()
    )
    return {r.name: (r.subs, r.cents) for r in daily._model_rows(links, previous, _RAN)}


def _to(link_id: int, destination: str, target: str, creator: str | None) -> ShortLink:
    return _link(link_id, (_NOV5, 10, 10)).model_copy(
        update={"destination": destination, "target": target, "creator": creator}
    )


def test_a_fan_site_account_is_counted_in_one_row_its_models(monkeypatch):
    stacked = _Site(ChatPlatform.STACKED, {"DaneWalker": (29, 77_486)})
    links = [
        _to(1, "https://www.stacked.com/@danewalker", "stacked", None),
        _to(2, "https://www.stacked.com/@DaneWalker", "stacked", "Dane"),
    ]

    rows = _rows(monkeypatch, links, [stacked])

    assert rows == {"Dane": (29, 77_486), "danewalker": (None, None)}


def test_unlinked_models_of_one_name_on_two_sites_add_up(monkeypatch):
    sites = [
        _Site(ChatPlatform.STACKED, {"sam": (10, 100_000)}),
        _Site(ChatPlatform.FANVUE, {"sam": (1, 500)}),
    ]

    assert _rows(monkeypatch, [], sites) == {"@sam": (11, 100_500)}


def test_a_block_too_long_for_one_message_is_split_between_lines_not_cut():
    lines = [f"https://www.instagram.com/account{n:03d}: 1.2k" for n in range(120)]

    events = daily._events(
        "views", "Views (last 24H)", ["\n".join(lines)], "2026-11-06"
    )

    assert len(events) == 2
    assert [e.title for e in events] == [
        "Views (last 24H) (1/2):",
        "Views (last 24H) (2/2):",
    ]
    assert "\n".join(e.text for e in events).split("\n") == lines
