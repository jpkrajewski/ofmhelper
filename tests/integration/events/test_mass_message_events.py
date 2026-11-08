"""The "Mass message sent" message to Telegram (features/reports/mass.py): sent
the moment a mass message is stored, one line per model since the last daily
report and no count (owner 2026-11-07: "it's just info"), each list taking the
place of the one before -- the daily report's Chatting part too -- like "Chats
unanswered". Fanvue is left out for now (`OFM_CHATTING_EVENTS_PLATFORMS_OFF`)."""

import os
import time
from http import HTTPStatus

os.environ.setdefault("SESSION_SECRET", "test-secret")

import pytest
from chat_platforms import StackedDriver
from fastapi.testclient import TestClient

import ofmhelpers.features.events.dispatch as dispatch_module
from ofmhelpers.db.stores import chats
from ofmhelpers.features.reports import daily, mass
from ofmhelpers.integrations.senders.telegram.fake import TOKEN, fake_telegram
from ofmhelpers.schemas.chatting import ChatDelivery, ChatPlatform, DeliveryKind
from ofmhelpers.web.main import app


@pytest.fixture(autouse=True)
def _telegram(fake_externals, monkeypatch):
    monkeypatch.setenv("OFM_TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("OFM_TELEGRAM_CHAT_IDS", "111")
    monkeypatch.setattr(dispatch_module, "_sleep", lambda _s: None)


@pytest.fixture
def stacked(monkeypatch) -> StackedDriver:
    driver = StackedDriver(monkeypatch)
    driver.connect()
    return driver


def _mass_by_webhook(stacked: StackedDriver, mass_id: str) -> None:
    raw, headers = stacked.signed(stacked.mass(mass_id))
    reply = TestClient(app).post("/events/stacked", content=raw, headers=headers)
    assert reply.status_code == HTTPStatus.OK


def _mass_stored(
    platform: ChatPlatform, model: str, event_id: str, at: float | None = None
) -> None:
    chats.save_notification(
        platform,
        ChatDelivery(kind=DeliveryKind.MASS, model_id="x", event_id=event_id, at=at),
        model,
    )


def _list(*models: str) -> str:
    return "\n".join(["Chatting:", "", *(f"Mass message sent: @{m}" for m in models)])


def test_a_mass_message_is_told_at_once_without_a_count(stacked):
    _mass_by_webhook(stacked, "mass-1")
    _mass_by_webhook(stacked, "mass-2")

    # The second names the same model: the same list, sent once.
    assert [m.text for m in fake_telegram().sent] == [_list(stacked.model)]


def test_each_new_list_takes_the_place_of_the_one_before(stacked):
    _mass_by_webhook(stacked, "mass-1")
    first = list(fake_telegram().sent)
    _mass_stored(ChatPlatform.STACKED, "anna_model", "mass-9")

    mass.tell_mass()

    assert fake_telegram().deleted == first
    assert fake_telegram().sent[-1].text == _list("anna_model", stacked.model)


def test_a_platform_left_out_tells_nothing(monkeypatch):
    monkeypatch.setenv("OFM_CHATTING_EVENTS_PLATFORMS_OFF", "fanvue")
    _mass_stored(ChatPlatform.FANVUE, "walker_dane", "fv-mass")

    mass.tell_mass()

    assert fake_telegram().sent == []


def test_the_daily_reports_chatting_part_takes_its_place(stacked, monkeypatch):
    """The report and the live list are one message in the chat: the newest."""
    now = time.time()
    monkeypatch.setenv("OFM_REPORTS_TIMEZONE", "UTC")
    monkeypatch.setenv("OFM_REPORTS_DAILY_AT", time.strftime("%H:%M", time.gmtime(now)))
    daily.run(now - 86400)  # the baseline, a day back
    _mass_stored(ChatPlatform.STACKED, stacked.model, "mass-1", at=now - 60)
    mass.tell_mass(now - 59)
    live = list(fake_telegram().sent)

    daily.run(now)

    chatting = [m for m in fake_telegram().sent if m.text.startswith("Chatting:")]
    assert [m.text for m in chatting] == [_list(stacked.model)] * 2
    assert fake_telegram().deleted == live
