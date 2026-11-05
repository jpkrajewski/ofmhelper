"""What only Stacked does: reactions it shows on a chat's messages alone, blocked fans,
destinations made here, one per creator. What every platform does is in
test_chat_platforms.py."""

import os
import time
from http import HTTPStatus

os.environ.setdefault("SESSION_SECRET", "test-secret")

import pytest
import requests
from chat_platforms import StackedDriver
from fastapi.testclient import TestClient

from ofmhelpers.db.stores import chats
from ofmhelpers.features.chatting.alerts import chat_alerts
from ofmhelpers.features.chatting.sync import run_when_due, sync
from ofmhelpers.features.chatting.webhooks import ensure_destinations
from ofmhelpers.integrations.stacked import fake as stacked_fake
from ofmhelpers.integrations.stacked.models import Message
from ofmhelpers.schemas.chatting import ChatDelivery, ChatPlatform, DeliveryKind
from ofmhelpers.web.main import app

pytestmark = pytest.mark.usefixtures("fake_externals")

LATE = 600
HOUR = 3600
PLATFORM = ChatPlatform.STACKED


@pytest.fixture
def side(monkeypatch):
    driver = StackedDriver(monkeypatch)
    driver.connect()
    return driver


def _popups():
    return [f"{p.text}: @{p.username}" for p in chat_alerts().popups]


def test_an_emoji_on_an_older_message_says_nothing_about_what_came_after(side):
    now = time.time()
    first = side.fan_writes("ann", now - LATE)
    side.fan_writes("ann", now - LATE + 60)
    sync(side.source)

    side.react("ann", int(first), now - 60)
    sync(side.source)

    assert _popups() == [f"Chats unanswered (1): @{side.model}"]


def test_an_answered_chat_is_not_read_for_reactions(side):
    now = time.time()
    side.fan_writes("ann", now - LATE)
    side.model_replies("ann", now - LATE + 60)
    sync(side.source)
    side.fake.calls.clear()

    sync(side.source)

    assert not [p for _, p in side.fake.calls if p.endswith("/messages")]


def test_a_waiting_chat_costs_one_read_of_one_message_a_round(side):
    now = time.time()
    side.fan_writes("ann", now - LATE)
    sync(side.source)
    side.fake.calls.clear()

    sync(side.source)

    assert len([p for _, p in side.fake.calls if p.endswith("/messages")]) == 1


def test_the_reactions_of_a_message_may_be_null():
    message = Message.model_validate(
        {"messageId": 1, "createdAt": 1_790_000_000_000, "reactions": None}
    )

    assert message.reactions == []


def test_a_fan_who_blocked_stops_waiting(side):
    side.fan_writes("ann", time.time() - LATE)
    sync(side.source)
    assert _popups() == [f"Chats unanswered (1): @{side.model}"]

    chat = side.fake.chats[side.model_id][0]
    fan = next(m for m in chat["members"] if m["userId"] == "ann")
    fan["isBlockingCreator"] = True
    sync(side.source)

    assert _popups() == []


def test_a_destination_is_made_once_with_every_event(side, monkeypatch):
    monkeypatch.setenv("OFM_CHATTING_WEBHOOK_BASE_URL", "https://example.invalid/")
    side.fake.webhooks.clear()
    for hook in chats.webhooks(PLATFORM):
        chats.save_webhook(PLATFORM, hook.model_copy(update={"webhook_id": "gone"}))

    ensure_destinations()
    ensure_destinations()

    made = side.fake.webhooks[stacked_fake.CREATOR]
    assert len(made) == 1
    assert made[0]["url"] == "https://example.invalid/events/stacked"
    [row] = chats.webhooks(PLATFORM)
    assert row.webhook_id == made[0]["id"]


def test_a_destination_at_another_address_is_never_replaced(side, monkeypatch):
    """Dev and production share the credentials."""
    monkeypatch.setenv("OFM_CHATTING_WEBHOOK_BASE_URL", "https://example.invalid/")
    theirs = {"id": "wh_theirs", "url": "https://elsewhere.invalid/x", "events": []}
    side.fake.webhooks[stacked_fake.CREATOR] = [theirs]

    ensure_destinations()

    assert side.fake.webhooks[stacked_fake.CREATOR] == [theirs]


def test_a_timeout_while_making_a_destination_stops_only_that_round(side, monkeypatch):
    monkeypatch.setenv("OFM_CHATTING_WEBHOOK_BASE_URL", "https://example.invalid/")
    side.fake.webhooks.clear()
    real = requests.Session.request

    def slow_on_post(self, method, url, **kwargs):
        if method == "POST":
            msg = "slow"
            raise requests.Timeout(msg)
        return real(self, method, url, **kwargs)

    monkeypatch.setattr(requests.Session, "request", slow_on_post)

    ensure_destinations()

    assert side.fake.webhooks.get(stacked_fake.CREATOR, []) == []


@pytest.fixture
def three_chats_are_a_bulk_send(monkeypatch):
    monkeypatch.setenv("OFM_CHATTING_MASS_MIN_CHATS", "3")


def _two_rounds() -> None:
    """The first reads what was sent, the next sees it stored."""
    run_when_due(PLATFORM)
    run_when_due(PLATFORM)


def _sent_to(side, fans, ago=60):
    for fan in fans:
        side.model_replies(fan, time.time() - ago)


def _mass_event(side, mass_id):
    raw, headers = side.signed(side.mass(mass_id))
    answer = TestClient(app).post(
        f"/events/{PLATFORM.value}", content=raw, headers=headers
    )
    assert answer.status_code == HTTPStatus.OK


@pytest.mark.usefixtures("three_chats_are_a_bulk_send")
def test_a_bulk_send_with_no_event_is_one_mass_message_popup(side):
    _sent_to(side, ["ann", "bob", "cat"])

    _two_rounds()

    assert _popups() == [f"Mass message sent: @{side.model}"]


@pytest.mark.usefixtures("three_chats_are_a_bulk_send")
def test_a_bulk_send_still_going_out_stays_one_popup(side):
    _sent_to(side, ["ann", "bob", "cat"])
    _two_rounds()

    _sent_to(side, ["dan", "eve", "fay"], ago=10)
    _two_rounds()

    assert _popups() == [f"Mass message sent: @{side.model}"]


@pytest.mark.usefixtures("three_chats_are_a_bulk_send")
def test_a_chatter_answering_two_chats_is_not_a_bulk_send(side):
    _sent_to(side, ["ann", "bob"])

    _two_rounds()

    assert _popups() == []


@pytest.mark.usefixtures("three_chats_are_a_bulk_send")
def test_many_messages_in_one_chat_are_not_a_bulk_send(side):
    _sent_to(side, ["ann", "ann", "ann"])

    _two_rounds()

    assert _popups() == []


@pytest.mark.usefixtures("three_chats_are_a_bulk_send")
def test_a_bulk_send_from_before_the_window_is_not_news(side):
    _sent_to(side, ["ann", "bob", "cat"], ago=HOUR)

    _two_rounds()

    assert _popups() == []


@pytest.mark.usefixtures("three_chats_are_a_bulk_send")
def test_the_next_bulk_send_after_the_gap_is_its_own_popup(side):
    side.model_replies("ann", time.time() - 60)
    run_when_due(PLATFORM)
    chats.save_notification(
        PLATFORM,
        ChatDelivery(
            kind=DeliveryKind.MASS,
            model_id="",
            event_id="bulk:earlier",
            at=time.time() - 2 * HOUR,
        ),
        side.model,
    )
    _sent_to(side, ["bob", "cat"])

    _two_rounds()

    assert _popups() == [f"Mass message sent: @{side.model}"] * 2


@pytest.mark.usefixtures("three_chats_are_a_bulk_send")
def test_stackeds_own_event_for_a_noticed_bulk_send_is_the_same_send(side):
    _sent_to(side, ["ann", "bob", "cat"])
    _two_rounds()

    _mass_event(side, "mass-1")

    assert _popups() == [f"Mass message sent: @{side.model}"]


@pytest.mark.usefixtures("three_chats_are_a_bulk_send")
def test_a_bulk_send_stacked_already_told_of_is_not_noticed_again(side):
    _mass_event(side, "mass-1")
    _sent_to(side, ["ann", "bob", "cat"])

    _two_rounds()

    assert _popups() == [f"Mass message sent: @{side.model}"]
