"""The daily report end to end: a baseline the first time, then since it: each
model's clicks, CTR, subscribers, conversion and sales (Stacked's API at the wire,
Fanvue's stored webhooks), each account's views, who sent a mass message -- as
the Telegram messages the group gets (both fakes at the wire)."""

import time
from datetime import UTC, datetime, timedelta

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
from ofmhelpers.features.reports import daily
from ofmhelpers.features.reports.schedule import Due
from ofmhelpers.integrations.senders.telegram.fake import TOKEN, fake_telegram
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


def _link(owner: int, destination: str, target: str, creator: str | None) -> int:
    with session_scope() as s:
        creator_id = None
        if creator:
            row = CreatorRow(user_id=owner, name=creator, created_at=NOW)
            s.add(row)
            s.flush()
            creator_id = row.id
        link = ShortLinkRow(
            user_id=owner,
            provider="oopsie",
            slug=f"s{abs(hash(destination)) % 10_000}",
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
    dane = _link(agency, "https://www.stacked.com/@fake_creator", "stacked", "Dane")
    rachel = _link(agency, "https://onlyfans.com/rachel.rosee/c1", "onlyfans", None)
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
Subs: 8
CTR: 80%
Conversion rate: 2.5%
Sales: $125

rachel.rosee:
Clicks: 50
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
