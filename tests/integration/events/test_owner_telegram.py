"""Each person links their own Telegram from /account, and their events go
there (owner 2026-11-15: "is it easy for new users to connect their telegram
bot?"; before, every owner's VA notices went to the agency's chats from the
server's .env)."""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ.setdefault("SESSION_SECRET", "test-secret")

import time
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from signin import make_user, sign_in

from ofmhelpers.db.stores import audit_log, event_targets
from ofmhelpers.features.events import Event
from ofmhelpers.features.events.dispatch import dispatch
from ofmhelpers.features.reports import asks
from ofmhelpers.integrations.senders.telegram.fake import TOKEN, fake_telegram
from ofmhelpers.schemas import Role
from ofmhelpers.web.main import app

AGENCY_CHAT = "111"
OWNER = "owner@example.com"


@pytest.fixture(autouse=True)
def _telegram(fake_externals, monkeypatch):
    monkeypatch.setenv("OFM_TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("OFM_TELEGRAM_CHAT_IDS", AGENCY_CHAT)
    monkeypatch.setenv("OFM_EVENTS_SENDERS_OFF", "")


def _connect(client: TestClient) -> str:
    """Connect Telegram on /account: the bot's link and its token."""
    answer = client.post("/account/telegram", follow_redirects=False)
    assert answer.status_code == 303
    url = urlsplit(answer.headers["location"])
    assert (url.netloc, url.path) == ("t.me", "/ofm_fake_bot")
    (token,) = parse_qs(url.query)["start"]
    return token


def _sent_to() -> list[tuple[str, str]]:
    return [(m.chat_id, m.text.splitlines()[0]) for m in fake_telegram().sent]


def test_a_person_links_their_chat_from_account_and_is_told_there():
    client = TestClient(app)
    owner = sign_in(client, OWNER)
    page = client.get("/account").text
    assert "Connect Telegram" in page

    token = _connect(client)
    fake_telegram().wrote(555, text=f"/start {token}")
    asks.answer_asks()

    assert event_targets.of(owner.id, "telegram") == "555"
    assert _sent_to() == [("555", "Connected")]
    assert "Disconnect Telegram" in client.get("/account").text
    (entry,) = audit_log.history("user_event_targets", f"{owner.id}:telegram")
    assert entry.changes["target"] == "set"  # where messages go: never in the log
    assert entry.via == "telegram"


def test_a_link_works_once():
    client = TestClient(app)
    owner = sign_in(client, OWNER)
    token = _connect(client)
    fake_telegram().wrote(555, text=f"/start {token}")
    fake_telegram().wrote(777, text=f"/start {token}")

    asks.answer_asks()

    assert event_targets.of(owner.id, "telegram") == "555"
    assert _sent_to() == [("555", "Connected"), ("777", "This link is used or too old")]


def test_disconnect_takes_it_back():
    client = TestClient(app)
    owner = sign_in(client, OWNER)
    event_targets.save(owner.id, "telegram", "555")

    client.post("/account/telegram/delete")

    assert event_targets.of(owner.id, "telegram") is None


@pytest.mark.parametrize(
    ("role", "linked", "goes_to"),
    [
        (Role.USER, "555", ["555"]),
        (Role.USER, None, []),
        (Role.ADMIN, None, [AGENCY_CHAT]),
        (Role.ADMIN, "555", ["555"]),
    ],
    ids=[
        "linked",
        "a user with none: nowhere",
        "an admin with none: the agency",
        "an admin linked",
    ],
)
def test_an_event_of_someone_goes_to_their_chat(role, linked, goes_to):
    owner = make_user(OWNER, role)
    if linked:
        event_targets.save(owner.id, "telegram", linked)

    dispatch(
        Event(name="vas.login", title="Ann logged in", owner_id=owner.id).model_dump(
            mode="json"
        )
    )

    assert [chat for chat, _ in _sent_to()] == goes_to


def test_the_agencys_events_still_go_to_the_agencys_chats():
    owner = make_user(OWNER)
    event_targets.save(owner.id, "telegram", "555")

    dispatch(
        Event(name="reports.daily", title="Report", at=time.time()).model_dump(
            mode="json"
        )
    )

    assert [chat for chat, _ in _sent_to()] == [AGENCY_CHAT]


def test_no_card_where_the_bot_is_not_read(monkeypatch):
    """Dev shares prod's bot and never reads it: nothing could link there."""
    monkeypatch.setenv("OFM_EVENTS_SENDERS_OFF", "telegram")
    client = TestClient(app)
    sign_in(client, OWNER)

    assert "Connect Telegram" not in client.get("/account").text
