import time
from urllib.parse import parse_qs, urlsplit

import pytest
import requests
from fastapi.testclient import TestClient
from signin import make_user, sign_in

from ofmhelpers.cache import get_redis
from ofmhelpers.db.audit_log import store as audit_log
from ofmhelpers.db.event_targets import store as event_targets
from ofmhelpers.db.users.schema import Role
from ofmhelpers.features.account.telegram import PendingLink
from ofmhelpers.features.events import Event
from ofmhelpers.features.events.dispatch import dispatch
from ofmhelpers.features.reports import asks
from ofmhelpers.integrations.senders.telegram import client as telegram_client_module
from ofmhelpers.integrations.senders.telegram.fake import TOKEN, fake_telegram
from ofmhelpers.web.main import app

AGENCY_CHAT = "111"
OWNER = "owner@example.com"


@pytest.fixture(autouse=True)
def _telegram(fake_externals, monkeypatch):
    monkeypatch.setenv("OFM_TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("OFM_TELEGRAM_CHAT_IDS", AGENCY_CHAT)
    monkeypatch.setenv("OFM_EVENTS_SENDERS_OFF", "")


def _connect(client: TestClient) -> str:
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
    assert entry.changes["target"] == "set"
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


def test_a_link_of_a_person_deleted_since_is_refused_and_read_once():
    get_redis().set(
        "telegram:link:gone-person",
        PendingLink(
            user_id=987_654_321, issued_at=time.time(), stamp=""
        ).model_dump_json(),
    )
    fake_telegram().wrote(555, text="/start gone-person")

    asks.answer_asks()
    asks.answer_asks()

    assert event_targets.of(987_654_321, "telegram") is None
    assert _sent_to() == [("555", "This link is used or too old")]


def test_the_bot_keeps_one_connection_for_its_calls(monkeypatch):
    made: list[requests.Session] = []
    real_session = requests.Session

    def counted() -> requests.Session:
        session = real_session()
        made.append(session)
        return session

    monkeypatch.setattr(telegram_client_module.requests, "Session", counted)
    client = telegram_client_module.telegram_client(TOKEN)

    client.me()
    client.send_message(AGENCY_CHAT, "one")
    telegram_client_module.telegram_client(TOKEN).send_message(AGENCY_CHAT, "two")

    assert len(made) == 1
    assert [m.text for m in fake_telegram().sent] == ["one", "two"]


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
    monkeypatch.setenv("OFM_EVENTS_SENDERS_OFF", "telegram")
    client = TestClient(app)
    sign_in(client, OWNER)

    assert "Connect Telegram" not in client.get("/account").text
