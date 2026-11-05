"""What only Stacked does: reactions it shows on a chat's messages alone, blocked fans,
destinations made here, one per creator. What every platform does is in
test_chat_platforms.py."""

import time

import pytest
import requests
from chat_platforms import StackedDriver

from ofmhelpers.db.stores import chats
from ofmhelpers.features.chatting.alerts import chat_alerts
from ofmhelpers.features.chatting.sync import sync
from ofmhelpers.features.chatting.webhooks import ensure_destinations
from ofmhelpers.integrations.stacked import fake as stacked_fake
from ofmhelpers.integrations.stacked.models import Message
from ofmhelpers.schemas.chatting import ChatPlatform

pytestmark = pytest.mark.usefixtures("fake_externals")

LATE = 600
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
