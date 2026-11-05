"""What only Fanvue does: logins that expire and rotate, the app-wide signing secret,
subscribers and sales, mass messages that name nobody. What every platform does is in
test_chat_platforms.py."""

import logging
import os
import re
import time
from http import HTTPStatus
from urllib.parse import parse_qs, urlsplit

os.environ.setdefault("SESSION_SECRET", "test-secret")

import pytest
from chat_platforms import FanvueDriver
from fastapi.testclient import TestClient
from signin import sign_in_admin
from sqlalchemy import text

from ofmhelpers.cache import delete_text, get_text
from ofmhelpers.db.session import session_scope
from ofmhelpers.db.stores import chats
from ofmhelpers.features.chatting.alerts import chat_alerts
from ofmhelpers.features.chatting.fanvue_connect import connect_url, finish
from ofmhelpers.features.chatting.fanvue_tokens import FanvueTokens
from ofmhelpers.features.chatting.sync import run_when_due, sync
from ofmhelpers.features.chatting.webhooks import (
    cover_models,
    mass_pending_key,
    sync_done_key,
)
from ofmhelpers.integrations.fanvue import fake as fanvue_fake
from ofmhelpers.integrations.fanvue.errors import FanvueAuthError, FanvueError
from ofmhelpers.schemas.chatting import ChatPlatform
from ofmhelpers.utils.outbound import FakeReply
from ofmhelpers.web.main import app

pytestmark = pytest.mark.usefixtures("fake_externals")

LATE = 600
PLATFORM = ChatPlatform.FANVUE
AGENCY = fanvue_fake.AGENCY
HANDLE = fanvue_fake.CREATOR_HANDLE


@pytest.fixture
def side(monkeypatch):
    driver = FanvueDriver(monkeypatch)
    driver.connect()
    return driver


@pytest.fixture
def client():
    return TestClient(app)


def _post(client, side, body):
    raw, headers = side.signed(body)
    return client.post("/events/fanvue", content=raw, headers=headers)


def _subscribed(event_id, creator=fanvue_fake.CREATOR):
    return {
        "id": event_id,
        "type": "creator.subscription.activated",
        "timestamp": "2026-11-05T00:00:00Z",
        "data": {"object": "subscription", "price": 999, "creator": {"uuid": creator}},
    }


def _payment(invoice, gross, source="post", reason="one_time", currency="USD"):
    return {
        "id": f"evt-{invoice}",
        "type": "creator.payment.succeeded",
        "timestamp": "2026-11-05T00:00:00Z",
        "data": {
            "object": "payment",
            "id": invoice,
            "source": source,
            "billing_reason": reason,
            "gross": gross,
            "currency": currency,
            "paid_at": "2026-11-05T01:00:00.000Z",
            "creator": {"uuid": fanvue_fake.CREATOR},
        },
    }


def _popups():
    return [f"{p.text}: @{p.username}" for p in chat_alerts().popups]


def _expire_the_token():
    with session_scope() as s:
        s.execute(text("update chat_accounts set expires_at = 0"))


# --- subscribers and sales --------------------------------------------------


def test_every_subscriber_is_their_own_popup(client, side):
    for event_id in ("sub-1", "sub-2", "sub-1"):
        assert _post(client, side, _subscribed(event_id)).status_code == HTTPStatus.OK

    assert _popups() == [f"New subscriber: @{HANDLE}"] * 2


@pytest.mark.parametrize(
    ("gross", "shown"),
    [(4999, False), (5000, False), (5001, True), (7500, True)],
)
def test_a_sale_needs_more_than_50_dollars_gross(client, side, gross, shown):
    assert _post(client, side, _payment("inv-1", gross)).status_code == HTTPStatus.OK

    assert bool(_popups()) is shown


def test_a_sale_says_what_sold(client, side):
    _post(client, side, _payment("inv-1", 7500))
    _post(client, side, _payment("inv-1", 7500))
    _post(client, side, _payment("inv-2", 5001, source="tip"))
    _post(
        client,
        side,
        _payment("inv-3", 12000, "subscription", "subscription_renewal"),
    )
    _post(client, side, _payment("inv-4", 9000, source="message", currency="EUR"))

    assert sorted(_popups()) == sorted(
        [
            f"Sold $75 post: @{HANDLE}",
            f"Sold $50.01 tip: @{HANDLE}",
            f"Sold $120 renewal: @{HANDLE}",
            f"Sold EUR 90 message: @{HANDLE}",
        ]
    )


@pytest.mark.parametrize(
    "kind",
    [
        "creator.follow.created",
        "creator.message.read_by_fan",
        "creator.subscription.renewed",
        "creator.subscription.deactivated",
        "creator.subscription.cancel_at_period_end_changed",
        "app.experience.action.payment.succeeded",
    ],
)
def test_the_events_tab_extras_are_answered_and_do_nothing(client, side, kind):
    body = {
        "id": "evt-x",
        "type": kind,
        "timestamp": "2026-11-05T00:00:00Z",
        "data": {"object": "x"},
    }

    assert _post(client, side, body).status_code == HTTPStatus.OK
    assert _popups() == []


# --- the app-wide secret ----------------------------------------------------


def test_fanvues_address_check_is_answered_unsigned(client, side):
    assert side.source
    reply = client.post("/events/fanvue", json={"type": "fanvue.webhook.verification"})
    assert reply.status_code == HTTPStatus.OK


def test_the_app_secret_is_accepted_before_any_creator_is_known(client, side, caplog):
    with session_scope() as s:
        s.execute(text("delete from chat_webhooks"))

    with caplog.at_level(logging.WARNING):
        reply = _post(client, side, _subscribed("sub-1"))

    assert reply.status_code == HTTPStatus.OK
    assert "delivery for an unknown model" in caplog.text
    assert _popups() == []


def test_the_sync_covers_every_creator_with_the_app_secret(side):
    with session_scope() as s:
        s.execute(text("delete from chat_webhooks"))

    sync(side.source)

    assert sorted(w.model_username for w in chats.webhooks(PLATFORM)) == [
        fanvue_fake.CREATOR_HANDLE,
        fanvue_fake.OTHER_HANDLE,
    ]
    assert {w.secret for w in chats.webhooks(PLATFORM)} == {side.secret}


def test_a_new_secret_reaches_the_rows_at_the_next_round(side, monkeypatch):
    monkeypatch.setenv("FANVUE_EVENT_SECRET", "whsec_rotated")

    cover_models(side.source, side.source.models().found)

    assert {w.secret for w in chats.webhooks(PLATFORM)} == {"whsec_rotated"}


# --- the login --------------------------------------------------------------


def test_an_expired_token_is_renewed_and_the_new_pair_kept(side):
    tokens = FanvueTokens()
    first = tokens.access_token(AGENCY)
    _expire_the_token()

    second = tokens.access_token(AGENCY)
    assert second != first
    assert side.fake.refreshes == 1

    _expire_the_token()
    assert tokens.access_token(AGENCY) not in (first, second)
    assert side.fake.refreshes == 2


def test_a_live_token_is_not_renewed(side):
    FanvueTokens().access_token(AGENCY)

    assert side.fake.refreshes == 0


def test_a_lost_answer_is_presented_again_at_once(side):
    down = FakeReply.json_body({"error": "down"}, HTTPStatus.BAD_GATEWAY)
    side.fake.token_failures.extend([down, down])
    _expire_the_token()

    assert FanvueTokens().access_token(AGENCY)
    assert chats.accounts(PLATFORM)[0].needs_reconnect is False


def test_a_token_endpoint_that_stays_down_does_not_end_the_login(side):
    down = FakeReply.json_body({"error": "down"}, HTTPStatus.BAD_GATEWAY)
    side.fake.token_failures.extend([down, down, down])
    _expire_the_token()

    with pytest.raises(FanvueError):
        FanvueTokens().access_token(AGENCY)
    assert chats.accounts(PLATFORM)[0].needs_reconnect is False

    assert FanvueTokens().access_token(AGENCY)


def test_a_dead_login_stops_quietly_and_says_so_on_every_page(side, caplog):
    side.fake.authorisation_dead = True
    _expire_the_token()

    with caplog.at_level(logging.WARNING), pytest.raises(FanvueAuthError):
        FanvueTokens().access_token(AGENCY)

    assert "The refresh chain is broken" in caplog.text
    assert chats.accounts(PLATFORM)[0].needs_reconnect is True
    [popup] = chat_alerts().popups
    assert (popup.kind, popup.text) == ("warn", "Connect Fanvue again")
    assert popup.url == "/chatting/fanvue"
    # A dead login is not asked again: that could only make things worse.
    refreshes = side.fake.refreshes
    with pytest.raises(FanvueAuthError):
        FanvueTokens().access_token(AGENCY)
    assert side.fake.refreshes == refreshes


def test_another_servers_login_is_never_spent(side, monkeypatch):
    """Dev runs on a copy of production's database, with the same vault key."""
    _expire_the_token()
    monkeypatch.setenv("APP_BASE_URL", "http://another-server.invalid/")

    assert chats.accounts(PLATFORM) == []
    assert side.source.models().found == []
    with pytest.raises(FanvueAuthError):
        FanvueTokens().access_token(AGENCY)
    assert side.fake.refreshes == 0


def test_an_agency_refused_is_not_an_outage(side):
    refused = FakeReply.json_body({"error": "Forbidden"}, HTTPStatus.FORBIDDEN)
    side.fake.fail_next.append(refused)

    reached = side.source.models()

    assert reached.found == []
    assert reached.failed is False


def test_a_read_failing_is_an_outage(side):
    down = FakeReply.json_body({"error": "down"}, HTTPStatus.BAD_GATEWAY)
    side.fake.fail_next.extend([down] * 4)

    assert side.source.models().failed is True


# --- connecting -------------------------------------------------------------


def test_connecting_keeps_the_login_for_this_server(monkeypatch):
    monkeypatch.setenv("FANVUE_CLIENT_ID", "fake-client")
    monkeypatch.setenv("FANVUE_CLIENT_SECRET", "fake-secret")
    state = parse_qs(urlsplit(connect_url()).query)["state"][0]

    assert finish(fanvue_fake.GOOD_CODE, state) is True

    [account] = chats.accounts(PLATFORM)
    assert (account.username, account.is_agency) == (fanvue_fake.AGENCY_HANDLE, True)


def test_a_trip_that_is_not_ours_or_a_bad_code_connects_nobody(monkeypatch):
    monkeypatch.setenv("FANVUE_CLIENT_ID", "fake-client")
    monkeypatch.setenv("FANVUE_CLIENT_SECRET", "fake-secret")

    assert finish(fanvue_fake.GOOD_CODE, "no-such-trip") is False

    state = parse_qs(urlsplit(connect_url()).query)["state"][0]
    assert finish("wrong-code", state) is False
    assert chats.accounts(PLATFORM) == []


def test_the_callback_page_says_how_it_went(client, monkeypatch):
    sign_in_admin(client)
    monkeypatch.setenv("FANVUE_CLIENT_ID", "fake-client")
    monkeypatch.setenv("FANVUE_CLIENT_SECRET", "fake-secret")

    start = client.get("/chatting/fanvue/connect", follow_redirects=False)
    assert start.status_code == HTTPStatus.SEE_OTHER
    query = parse_qs(urlsplit(start.headers["location"]).query)
    assert query["code_challenge_method"] == ["S256"]

    back = client.get(
        "/chatting/fanvue/callback",
        params={"code": fanvue_fake.GOOD_CODE, "state": query["state"][0]},
        follow_redirects=False,
    )
    assert re.search(r"note=connected$", back.headers["location"])

    refused = client.get("/chatting/fanvue/callback", follow_redirects=False)
    assert re.search(r"note=failed$", refused.headers["location"])


# --- reading ----------------------------------------------------------------


def test_every_read_of_messages_asks_not_to_mark_them_read(side):
    side.fan_writes("ann", time.time() - LATE)
    side.fan_writes("bob", time.time() - LATE)

    sync(side.source)

    reads = [q for _, path, q in side.fake.calls if path.endswith("/messages")]
    assert len(reads) == 2
    assert {q["markAsRead"] for q in reads} == {"false"}


def test_many_chats_answered_at_once_are_not_a_mass_message_here(side, monkeypatch):
    """Fanvue says when it sends one."""
    monkeypatch.setenv("OFM_CHATTING_MASS_MIN_CHATS", "2")
    now = time.time()
    side.model_replies("ann", now - 60)
    side.model_replies("bob", now - 60)

    run_when_due(PLATFORM)
    run_when_due(PLATFORM)

    assert _popups() == []


def test_a_chat_past_the_lookback_waits_for_the_full_round(side):
    run_when_due(PLATFORM)
    side.fan_writes("ann", time.time() - 40 * 86400)

    run_when_due(PLATFORM)
    assert _popups() == []

    delete_text(sync_done_key(PLATFORM))
    run_when_due(PLATFORM)
    assert _popups() == [f"Chats unanswered (1): @{HANDLE}"]


def test_a_mass_message_answers_only_once_a_full_round_has_read_it(client, side):
    now = time.time()
    side.fan_writes("ann", now - LATE)
    run_when_due(PLATFORM)
    assert _popups() == [f"Chats unanswered (1): @{HANDLE}"]
    assert get_text(sync_done_key(PLATFORM)) == "1"

    side.fake.add_message(
        side.model_id, "ann", from_fan=False, at=now - 60, message_type="BROADCAST"
    )
    assert _post(client, side, side.mass("mass-1")).status_code == HTTPStatus.OK
    assert get_text(mass_pending_key(PLATFORM)) == "1"

    run_when_due(PLATFORM)

    assert get_text(mass_pending_key(PLATFORM)) is None
    assert _popups() == [f"Mass message sent: @{HANDLE}"]


def test_a_scheduled_promotion_counts_like_a_mass_message(side):
    now = time.time()
    side.fan_writes("ann", now - LATE)
    sync(side.source)
    assert _popups() == [f"Chats unanswered (1): @{HANDLE}"]

    side.fake.add_message(
        side.model_id,
        "ann",
        from_fan=False,
        at=now - 60,
        message_type="GHOST_PROMOTION",
    )
    sync(side.source)

    assert _popups() == []


def test_an_unsent_mass_message_answers_nobody(side):
    now = time.time()
    side.fan_writes("ann", now - LATE)
    sync(side.source)

    side.fake.add_message(
        side.model_id,
        "ann",
        from_fan=False,
        at=now - 60,
        message_type="BROADCAST",
        status="UNSENT",
    )
    sync(side.source)

    assert _popups() == [f"Chats unanswered (1): @{HANDLE}"]


def test_a_chat_fanvue_will_not_open_does_not_stop_the_round(side):
    side.fan_writes("ann", time.time() - LATE)
    side.fan_writes("bob", time.time() - LATE)
    refused = FakeReply.json_body(
        {"error": "Invalid user UUID"}, HTTPStatus.BAD_REQUEST
    )
    real = side.fake.handle

    def refuse_ann(request):
        return refused if "/ann/messages" in (request.url or "") else real(request)

    side.fake.handle = refuse_ann

    sync(side.source)

    assert _popups() == [f"Chats unanswered (1): @{HANDLE}"]
