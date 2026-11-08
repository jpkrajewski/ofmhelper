"""The daily report end to end: a baseline the first time, then since it: each
model's clicks, CTR, subscribers, conversion and sales (Stacked's API at the wire,
Fanvue's stored webhooks), each account's views, who sent a mass message -- as
the Telegram messages the group gets (both fakes at the wire)."""

import logging
import time
from datetime import UTC, datetime, timedelta
from http import HTTPStatus

import pytest

import ofmhelpers.features.events.dispatch as dispatch_module
from ofmhelpers.cache import get_redis
from ofmhelpers.db.models import (
    ChatThreadRow,
    CreatorRow,
    ShortLinkDayRow,
    ShortLinkRow,
)
from ofmhelpers.db.session import session_scope
from ofmhelpers.db.stores import chats, report_runs, users
from ofmhelpers.db.stores.tracked_accounts import list_accounts, record_scan, track
from ofmhelpers.features.reports import asks, daily
from ofmhelpers.features.reports.schedule import Due
from ofmhelpers.integrations.senders.telegram.fake import TOKEN, fake_telegram
from ofmhelpers.integrations.short_links.fake import OOPSIE_FAKE_KEY, fake_oopsie
from ofmhelpers.integrations.socials import AccountProfile, AccountStats, PostStat
from ofmhelpers.integrations.stacked.fake import CREATOR, CREATOR_KEY, fake_stacked
from ofmhelpers.schemas.chatting import ChatDelivery, ChatPlatform, DeliveryKind

_DAY = 86400
NOW = time.time()
T0 = NOW - _DAY
TODAY = datetime.fromtimestamp(NOW, UTC).date()
YESTERDAY = TODAY - timedelta(days=1)
_STACKED_MODEL = "fake_creator"  # the fake's CREATOR's username


@pytest.fixture(autouse=True)
def _services(fake_externals, monkeypatch):
    monkeypatch.setenv("OFM_TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("OFM_TELEGRAM_CHAT_IDS", "111")
    monkeypatch.setenv("STACKED_API_KEY_DANE", CREATOR_KEY)
    # Fanvue's webhooks reach this server: its numbers are real.
    monkeypatch.setenv("FANVUE_EVENT_SECRET", "fanvue-secret")
    # On UTC's clock at this very minute: the day before is the baseline, now the
    # report (no summer-time day can move either).
    monkeypatch.setenv("OFM_REPORTS_TIMEZONE", "UTC")
    monkeypatch.setenv(
        "OFM_REPORTS_DAILY_AT", datetime.fromtimestamp(NOW, UTC).strftime("%H:%M")
    )
    monkeypatch.setattr(dispatch_module, "_sleep", lambda _s: None)


def _written() -> None:
    """Rows written straight to the tables: the repositories' cached reads go."""
    get_redis().flushdb()


def _link(
    owner: int, destination: str, target: str, creator: str | None, slug: str
) -> int:
    with session_scope() as s:
        creator_id = None
        if creator:
            row = (
                s.query(CreatorRow).filter_by(user_id=owner, name=creator).one_or_none()
            )
            if row is None:
                row = CreatorRow(user_id=owner, name=creator, created_at=NOW)
                s.add(row)
                s.flush()
            creator_id = row.id
        link = ShortLinkRow(
            user_id=owner,
            provider="oopsie",
            slug=slug,
            remote_id=slug,
            domain="oop.si",
            destination=destination,
            target=target,
            creator_id=creator_id,
            status="active",
            created_at=NOW,
            updated_at=NOW,
        )
        s.add(link)
        s.flush()
        link_id = link.id
    _written()
    return link_id


def _days(link_id: int, *days: tuple[object, int, int]) -> None:
    with session_scope() as s:
        s.query(ShortLinkDayRow).filter_by(link_id=link_id).delete()
        for day, views, clicks in days:
            s.add(ShortLinkDayRow(link_id=link_id, day=day, views=views, clicks=clicks))
    _written()


def _scanned(handle: str, views: int, model: str | None, now: float = NOW) -> None:
    track("instagram", handle, model)
    account = next(a for a in list_accounts() if a.handle == handle)
    record_scan(
        account.id,
        AccountStats(
            profile=AccountProfile(
                handle=handle, display_name=handle, followers=1, following=1
            ),
            posts=[
                PostStat(
                    id=f"{handle}-new", url="u", views=views, posted_at=NOW - 3600
                ),
                PostStat(
                    id=f"{handle}-old", url="u", views=9, posted_at=NOW - 3 * _DAY
                ),
            ],
            scanned_at=NOW,
        ),
    )


def _fanvue(kind: DeliveryKind, event_id: str, at: float, cents: int | None = None):
    delivery = ChatDelivery(
        kind=kind,
        model_id="fv",
        event_id=event_id,
        amount_cents=cents,
        currency="USD",
        detail="post",
        at=at,
    )
    if kind == DeliveryKind.SALE:
        chats.save_sale(ChatPlatform.FANVUE, delivery, "walker_dane")
    else:
        chats.save_notification(ChatPlatform.FANVUE, delivery, "walker_dane")


@pytest.fixture
def agency() -> int:
    return users.agency_id()


def _the_day_before(agency: int) -> tuple[int, int]:
    dane = _link(
        agency, "https://www.stacked.com/@fake_creator", "stacked", "Dane", "dane1"
    )
    rachel = _link(
        agency, "https://onlyfans.com/rachel.rosee/c1", "onlyfans", None, "rachel1"
    )
    _days(dane, (YESTERDAY, 100, 80))
    with session_scope() as s:
        s.add(
            ChatThreadRow(
                platform="fanvue",
                channel_id="c1",
                model_username="walker_dane",
                last_message_id="m1",
            )
        )
    return dane, rachel


def _what_came_in(dane: int, rachel: int) -> None:
    _days(dane, (YESTERDAY, 300, 250), (TODAY, 200, 150))
    _days(rachel, (TODAY, 50, 50))
    for n in range(8):
        fake_stacked().subscribed(CREATOR, NOW - 3600 - n)
    fake_stacked().subscribed(CREATOR, T0 - 60)  # before the window
    fake_stacked().sold(CREATOR, 10_000, NOW - 600)
    fake_stacked().sold(CREATOR, 2_500, NOW - 500)
    _fanvue(DeliveryKind.SUBSCRIBED, "s1", NOW - 3600)
    _fanvue(DeliveryKind.SUBSCRIBED, "s2", NOW - 1800)
    _fanvue(DeliveryKind.SUBSCRIBED, "s0", T0 - 100)
    _fanvue(DeliveryKind.SALE, "p1", NOW - 600, 5000)
    _fanvue(DeliveryKind.SALE, "p2", NOW - 300, 99)
    chats.save_notification(
        ChatPlatform.STACKED,
        ChatDelivery(
            kind=DeliveryKind.MASS, model_id="x", event_id="bulk:1", at=NOW - 7200
        ),
        _STACKED_MODEL,
    )
    # Fanvue is left out of chatting messages for now.
    chats.save_notification(
        ChatPlatform.FANVUE,
        ChatDelivery(kind=DeliveryKind.MASS, model_id="fv", event_id="m1", at=NOW - 60),
        "walker_dane",
    )
    _scanned("danywalking", 1_300_000, "Dane")
    _scanned("walkerronrig", 500_000, "Dane")
    _scanned("rachell.bing", 1_800, "Rachel")
    _scanned("xliftdon", 0, None)


_MODELS = """Clicks / Rev (Last 24H):

Dane:
Clicks: 320
- oop.si/dane1: 320
Subs: 8
CTR: 80%
Conversion rate: 2.5%
Sales: $125

rachel.rosee:
Clicks: 50
- oop.si/rachel1: 50
Subs: —
CTR: 100%
Conversion rate: —
Sales: —

@walker_dane:
Clicks: —
Subs: 2
CTR: —
Conversion rate: —
Sales: $50.99"""

_VIEWS = """Views (last 24H):

Dane (Total: 1.8M):
https://www.instagram.com/danywalking: 1.3M
https://www.instagram.com/walkerronrig: 500k

Rachel (Total: 1.8k):
https://www.instagram.com/rachell.bing: 1.8k

No model (Total: 0):
https://www.instagram.com/xliftdon: 0"""

_CHATTING = """Chatting:

Mass message sent: @fake_creator"""


def test_the_first_look_takes_a_baseline_and_sends_nothing(agency):
    _the_day_before(agency)

    assert daily.run(T0) == Due.BASELINE

    assert fake_telegram().sent == []


def test_the_report_counts_everything_since_the_baseline(agency):
    dane, rachel = _the_day_before(agency)
    daily.run(T0)
    _what_came_in(dane, rachel)

    assert daily.run(NOW) == Due.SEND

    assert [m.text for m in fake_telegram().sent] == [_MODELS, _VIEWS, _CHATTING]
    assert {m.chat_id for m in fake_telegram().sent} == {"111"}


def test_it_goes_out_once_a_day(agency):
    dane, rachel = _the_day_before(agency)
    daily.run(T0)
    _what_came_in(dane, rachel)
    daily.run(NOW)

    assert daily.run(NOW + 60) == Due.NOTHING

    assert len(fake_telegram().sent) == 3


def test_switched_off_it_does_nothing(agency, monkeypatch):
    from ofmhelpers.features.jobs.sweeps import sweeps

    monkeypatch.setenv("OFM_REPORTS_DAILY_ENABLED", "false")

    report = next(s for s in sweeps() if s.name == "daily report")

    assert report.enabled() is False


def test_nobody_listening_reads_and_counts_nothing(agency, monkeypatch):
    _the_day_before(agency)
    monkeypatch.delenv("OFM_TELEGRAM_BOT_TOKEN")

    assert daily.run(T0) == Due.NOTHING

    assert report_runs.last_run(daily.NAME) is None


def test_a_failed_run_waits_before_trying_again(agency, monkeypatch):
    dane, rachel = _the_day_before(agency)
    daily.run(T0)
    _what_came_in(dane, rachel)

    def broken(*_args):
        msg = "provider answered nonsense"
        raise RuntimeError(msg)

    monkeypatch.setattr(daily, "build", broken)
    with pytest.raises(RuntimeError, match="nonsense"):
        daily.run(NOW)

    assert daily.run(NOW + 60) == Due.NOTHING
    assert fake_telegram().sent == []


def test_fanvue_without_its_webhooks_here_says_nothing_not_zero(agency, monkeypatch):
    monkeypatch.delenv("FANVUE_EVENT_SECRET")
    dane, rachel = _the_day_before(agency)
    daily.run(T0)
    _what_came_in(dane, rachel)

    daily.run(NOW)

    walker = fake_telegram().sent[0].text.split("@walker_dane:")[1]
    assert "Subs: —" in walker
    assert "Sales: —" in walker


# --- `money` typed to the bot (features/reports/asks.py) ------------------------


_MONEY = """Clicks / Rev (Last 24H):

Dane:
Clicks: 90
- oop.si/dane1: 90
Subs: 8
CTR: 90%
Conversion rate: 8.9%
Sales: $125

@walker_dane:
Clicks: —
Subs: 2
CTR: —
Conversion rate: —
Sales: $50.99

rachel.rosee:
Clicks: 0
Subs: —
CTR: —
Conversion rate: —
Sales: —"""


@pytest.fixture
def live(agency, monkeypatch) -> None:
    """The last 24 hours as the providers tell them: Dane's Oopsie page clicked
    3 hours ago (and 30 hours ago: outside), Rachel's never; the fan sites'
    subs and sales of `_what_came_in`."""
    monkeypatch.setenv("OOPSIE_API_KEY_ADMIN", OOPSIE_FAKE_KEY)
    dane, rachel = _the_day_before(agency)
    _what_came_in(dane, rachel)
    fake_oopsie().add_page("dane1", "https://www.stacked.com/@fake_creator")
    hour = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    fake_oopsie().set_hour_traffic("dane1", hour - timedelta(hours=3), 100, 90)
    fake_oopsie().set_hour_traffic("dane1", hour - timedelta(hours=30), 500, 500)


@pytest.mark.parametrize("typed", ["money", "Money ", "/money", "/money@ofm_fake_bot"])
@pytest.mark.usefixtures("live")
def test_money_typed_in_the_group_is_answered_with_the_last_24_hours(typed):
    """Clicks asked of the provider by the hour, subs and sales of the same 24
    hours, in the chat that asked only; the daily report untouched (no run
    taken, nothing synced)."""
    fake_telegram().wrote(111, text=typed)

    asks.answer_asks()

    assert [(m.chat_id, m.text) for m in fake_telegram().sent] == [("111", _MONEY)]
    assert report_runs.last_run(daily.NAME) is None


def test_an_answered_ask_is_logged_without_its_chat(agency, caplog):
    """Where a message goes is never logged (senders/CLAUDE.md); prod's first
    asks wrote the group's id to Logfire."""
    fake_telegram().wrote(111, text="money")
    with caplog.at_level(logging.INFO, logger=asks.__name__):
        asks.answer_asks()

    lines = [r.getMessage() for r in caplog.records if r.name == asks.__name__]
    assert lines == ["telegram: 'money' asked"]


def test_money_from_a_chat_not_listed_or_typed_long_ago_is_not_answered(agency):
    daily.run(T0)
    fake_telegram().wrote(999, text="money")
    fake_telegram().wrote(111, text="money", sent_at=time.time() - 3600)
    fake_telegram().wrote(111, text="how much money")

    asks.answer_asks()

    assert fake_telegram().sent == []


def test_an_ask_is_answered_once(agency):
    daily.run(T0)
    fake_telegram().wrote(111, text="money")

    asks.answer_asks()
    asks.answer_asks()

    assert len(fake_telegram().sent) == 1
    assert fake_telegram().sent[0].text.startswith("Clicks / Rev (Last 24H):")


@pytest.mark.usefixtures("live")
def test_money_needs_no_report_before_it():
    fake_telegram().wrote(111, text="money")

    asks.answer_asks()

    assert [m.text for m in fake_telegram().sent] == [_MONEY]


def test_a_command_for_another_bot_is_not_answered(agency):
    daily.run(T0)
    fake_telegram().wrote(111, text="/money@some_other_bot")

    asks.answer_asks()

    assert fake_telegram().sent == []


def test_an_unreadable_update_is_skipped_and_the_next_ask_answered(agency):
    daily.run(T0)
    fake_telegram().odd_update()
    fake_telegram().wrote(111, text="money")

    asks.answer_asks()
    asks.answer_asks()

    assert len(fake_telegram().sent) == 1


@pytest.mark.usefixtures("live")
def test_money_asks_the_providers_and_syncs_nothing(monkeypatch):
    synced: list[int] = []
    monkeypatch.setattr(daily, "sync_links", lambda user, _p: synced.append(user))
    for _ in range(2):
        fake_telegram().wrote(111, text="money")
        asks.answer_asks()

    assert [m.text for m in fake_telegram().sent] == [_MONEY] * 2
    assert synced == []


@pytest.mark.usefixtures("live")
def test_a_provider_that_cannot_tell_now_is_named(monkeypatch):
    monkeypatch.setenv("OOPSIE_API_KEY_ADMIN", "gmp_live_wrong")
    fake_telegram().wrote(111, text="money")

    asks.answer_asks()

    text = fake_telegram().sent[0].text
    assert text.endswith("Oopsie: clicks could not be read right now")
    assert "Clicks: 0" in text.split("Dane:\n", 1)[1]


def test_a_failed_read_waits_before_the_next(monkeypatch):
    from ofmhelpers.integrations.senders.telegram.fake import error

    fake_telegram().fail_next.append(error(HTTPStatus.BAD_GATEWAY, "Bad Gateway"))

    asks.answer_asks()
    assert asks.pause_s() == 20

    asks.answer_asks()
    assert asks.pause_s() == 1


def test_a_report_block_in_parts_keeps_each_part_in_its_own_place():
    chunks = ["x" * 2500, "y" * 2500]

    parts = daily._events("reports.daily.chatting", "Chatting", chunks, "k", "slot")

    assert [p.replaces for p in parts] == ["slot", "slot:2"]


# --- each link's clicks under its model (owner 2026-11-08) ------------------------


def _dane_links(agency: int, *slugs: str) -> dict[str, int]:
    return {
        slug: _link(
            agency,
            f"https://www.stacked.com/@fake_creator/{slug}",
            "stacked",
            "Dane",
            slug,
        )
        for slug in slugs
    }


def _dane_block() -> list[str]:
    text = fake_telegram().sent[0].text
    return text.split("Dane:\n", 1)[1].split("\n\n", 1)[0].split("\n")


def test_each_link_of_a_model_is_listed_under_its_clicks_most_first(agency):
    links = _dane_links(agency, "danywalking", "walkerronrig", "danenydon")
    daily.run(T0)
    _days(links["walkerronrig"], (TODAY, 120, 120))
    _days(links["danywalking"], (TODAY, 3800, 3800))

    daily.run(NOW)

    # danenydon got no clicks: not listed.
    assert _dane_block()[:4] == [
        "Clicks: 3.9k",
        "- oop.si/danywalking: 3.8k",
        "- oop.si/walkerronrig: 120",
        "Subs: 0",
    ]


def test_past_ten_links_the_rest_are_one_line(agency):
    links = _dane_links(agency, *(f"link{n:02}" for n in range(1, 13)))
    daily.run(T0)
    for n, link_id in enumerate(links.values(), start=1):
        _days(link_id, (TODAY, n, n))

    daily.run(NOW)

    block = _dane_block()
    assert block[1:12] == [
        *(f"- oop.si/link{n:02}: {n}" for n in range(12, 2, -1)),
        "- and 2 more",
    ]


def test_a_model_whose_links_got_no_clicks_lists_none(agency):
    _dane_links(agency, "danywalking")
    daily.run(T0)

    daily.run(NOW)

    assert _dane_block()[:2] == ["Clicks: 0", "Subs: 0"]
