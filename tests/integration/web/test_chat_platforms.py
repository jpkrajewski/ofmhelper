"""Every chat platform through the one `ChatSource` interface: the same tests, the same
words, for each. What one platform does differently lives in tests/chat_platforms.py;
a test here failing for one platform only means the interface leaks."""

import os
import time
from datetime import UTC, datetime, timedelta
from http import HTTPStatus

os.environ.setdefault("SESSION_SECRET", "test-secret")

import pytest
from chat_platforms import PLATFORMS
from fastapi.testclient import TestClient
from signin import sign_in, sign_in_admin

from ofmhelpers.cache import get_text
from ofmhelpers.db.stores import chats
from ofmhelpers.features.chatting.alerts import chat_alerts
from ofmhelpers.features.chatting.sync import run_when_due, sync
from ofmhelpers.features.chatting.webhooks import (
    ensure_destinations,
    models_delivering,
    sync_done_key,
)
from ofmhelpers.integrations.chat_source import ChatSourceError
from ofmhelpers.schemas.chatting import DeliveryKind, ReadScope
from ofmhelpers.web.main import app

pytestmark = pytest.mark.usefixtures("fake_externals")

MIN = 60
LATE = 10 * MIN
DAY = 24 * 60 * MIN


@pytest.fixture(params=sorted(PLATFORMS))
def side(request, monkeypatch):
    driver = PLATFORMS[request.param](monkeypatch)
    driver.connect()
    return driver


@pytest.fixture
def client():
    return TestClient(app)


def _post(client, side, body, secret=None, age_s=0):
    raw, headers = side.signed(body, secret=secret, age_s=age_s)
    return client.post(f"/events/{side.platform.value}", content=raw, headers=headers)


def _fan_waits(client, side, fan, ago=LATE):
    """A fan's message arrives by webhook, `ago` seconds old."""
    message_id = side.new_id()
    body = side.message(fan, message_id, from_fan=True, at=time.time() - ago)
    assert _post(client, side, body).status_code == HTTPStatus.OK
    return message_id


def _model_replies(client, side, fan, ago=0):
    body = side.message(fan, side.new_id(), from_fan=False, at=time.time() - ago)
    assert _post(client, side, body).status_code == HTTPStatus.OK


def _popups():
    return [f"{p.text}: @{p.username}" for p in chat_alerts().popups]


def _scope(side):
    return ReadScope(
        seen=chats.last_read(side.platform),
        oldest=_long_ago(),
        open_channels=chats.open_channels(side.platform),
        thorough=True,
    )


def _long_ago():
    return datetime.now(UTC) - timedelta(days=1)


def _model_of(side):
    return next(m for m in side.source.models().found if m.username == side.model)


# --- the interface itself ---------------------------------------------------


def test_a_platform_finds_its_model(side):
    assert side.source.platform == side.platform
    assert side.source.configured()
    assert side.model in [m.username for m in side.source.models().found]


def test_a_read_gives_what_was_said_oldest_first(side):
    now = time.time()
    side.fan_writes("ann", now - 300)
    side.model_replies("ann", now - 200)
    side.fan_writes("ann", now - 100)

    reads = list(side.source.new_reads(_model_of(side), _scope(side)))

    assert [r.channel_id for r in reads] == [side.channel("ann")]
    assert [e.from_fan for e in reads[0].events] == [True, False, True]
    assert [round(e.at) for e in reads[0].events] == [
        round(now - 300),
        round(now - 200),
        round(now - 100),
    ]


def test_a_chat_with_nothing_new_is_not_read_again(side):
    side.fan_writes("ann", time.time() - 100)
    side.model_replies("ann", time.time() - 50)
    sync(side.source)

    assert list(side.source.new_reads(_model_of(side), _scope(side))) == []


def test_a_signature_is_believed_only_when_it_is_right_and_fresh(side):
    body = side.event_nobody_uses()
    raw, good = side.signed(body)
    assert side.source.verified(side.secret, raw, good)

    _, forged = side.signed(body, secret=side.secret[:-4] + "AAA=")
    assert not side.source.verified(side.secret, raw, forged)

    _, old = side.signed(body, age_s=3600)
    assert not side.source.verified(side.secret, raw, old)

    assert not side.source.verified(side.secret, raw + b" ", good)


def test_deliveries_read_the_same_for_every_platform(side):
    now = time.time()
    raw, _ = side.signed(side.message("ann", "501", from_fan=True, at=now))
    [fan] = side.source.deliveries(raw)
    assert fan.kind == DeliveryKind.MESSAGE
    assert fan.model_id == side.model_id
    assert fan.read.channel_id == side.channel("ann")
    assert [e.from_fan for e in fan.read.events] == [True]
    assert round(fan.read.events[0].at) == round(now)

    raw, _ = side.signed(side.message("ann", "502", from_fan=False, at=now))
    [reply] = side.source.deliveries(raw)
    assert [e.from_fan for e in reply.read.events] == [False]

    raw, _ = side.signed(side.deleted("ann", "503", now))
    [deleted] = side.source.deliveries(raw)
    assert (deleted.kind, deleted.message_id) == (DeliveryKind.DELETED, "503")

    raw, _ = side.signed(side.mass("mass-1"))
    [mass] = side.source.deliveries(raw)
    assert (mass.kind, mass.event_id) == (DeliveryKind.MASS, "mass-1")


def test_an_event_nobody_uses_means_nothing(side):
    raw, _ = side.signed(side.event_nobody_uses())
    assert side.source.deliveries(raw) == []


def test_reading_never_marks_anything_read(side):
    side.fan_writes("ann", time.time() - 100)
    sync(side.source)

    calls = side.reads()
    assert calls
    assert {method for method, _ in calls} == {"GET"}
    assert not [path for _, path in calls if path.endswith("/read")]


# --- /events/{platform} into the pop-up -------------------------------------


def test_a_fan_past_the_limit_gets_the_model_a_popup(client, side):
    _fan_waits(client, side, "ann")

    assert _popups() == [f"Chats unanswered (1): @{side.model}"]


def test_a_fan_within_the_limit_is_not_reported_yet(client, side):
    _fan_waits(client, side, "ann", ago=MIN)

    assert _popups() == []


def test_one_popup_per_model_counting_fans_not_messages(client, side):
    _fan_waits(client, side, "ann")
    _fan_waits(client, side, "ann", ago=LATE - MIN)
    _fan_waits(client, side, "bob")

    assert _popups() == [f"Chats unanswered (2): @{side.model}"]
    [popup] = chat_alerts().popups
    assert popup.kind == "warn"
    assert popup.url == f"/chatting/{side.platform.value}/{side.model}"
    assert len(popup.event_ids) == 2


def test_the_number_goes_down_as_chatters_answer(client, side):
    for fan in ("ann", "bob", "cid"):
        _fan_waits(client, side, fan)
    assert _popups() == [f"Chats unanswered (3): @{side.model}"]

    _model_replies(client, side, "bob")
    assert _popups() == [f"Chats unanswered (2): @{side.model}"]

    _model_replies(client, side, "ann")
    _model_replies(client, side, "cid")
    assert _popups() == []


def test_a_reply_arriving_before_the_fans_message_still_answers_it(client, side):
    _model_replies(client, side, "ann", ago=MIN)
    _fan_waits(client, side, "ann", ago=LATE)

    assert _popups() == []


def test_a_fan_writing_again_keeps_their_place_in_line(client, side):
    _fan_waits(client, side, "ann", ago=LATE)
    first = chat_alerts().popups[0].event_ids

    _fan_waits(client, side, "ann", ago=MIN)

    assert chat_alerts().popups[0].event_ids == first


def test_the_same_delivery_twice_counts_once(client, side):
    body = side.message("ann", side.new_id(), from_fan=True, at=time.time() - LATE)
    for _ in range(2):
        assert _post(client, side, body).status_code == HTTPStatus.OK

    report = chats.fan_messages(side.platform, side.model, since=0)
    assert len(report) == 1


def test_a_deleted_fan_message_stops_waiting(client, side):
    message_id = _fan_waits(client, side, "ann")

    body = side.deleted("ann", message_id, time.time())
    assert _post(client, side, body).status_code == HTTPStatus.OK

    assert _popups() == []


def test_a_destination_at_a_longer_address_is_heard(client, side):
    """A platform set up on its own site sends to whatever address was typed there."""
    body = side.message("ann", side.new_id(), from_fan=True, at=time.time() - LATE)
    raw, headers = side.signed(body)

    answer = client.post(
        f"/events/{side.platform.value}/creator-events", content=raw, headers=headers
    )

    assert answer.status_code == HTTPStatus.OK
    assert _popups() == [f"Chats unanswered (1): @{side.model}"]


def test_a_longer_address_does_not_get_around_the_signature(client, side):
    body = side.message("ann", side.new_id(), from_fan=True, at=time.time() - LATE)
    raw, headers = side.signed(body, secret=side.secret[:-4] + "AAA=")

    answer = client.post(
        f"/events/{side.platform.value}/creator-events", content=raw, headers=headers
    )

    assert answer.status_code == HTTPStatus.UNAUTHORIZED
    assert _popups() == []


def test_a_fan_waiting_for_days_is_found_on_the_first_read(side):
    side.fan_writes("ann", time.time() - 40 * DAY)

    run_when_due(side.platform)

    assert _popups() == [f"Chats unanswered (1): @{side.model}"]


def test_each_mass_message_is_its_own_popup_and_only_once(client, side):
    for mass_id in ("mass-1", "mass-2", "mass-1"):
        assert _post(client, side, side.mass(mass_id)).status_code == HTTPStatus.OK

    assert _popups() == [f"Mass message sent: @{side.model}"] * 2


def test_a_forged_or_old_delivery_changes_nothing(client, side):
    body = side.message("ann", side.new_id(), from_fan=True, at=time.time() - LATE)

    wrong = side.secret[:-4] + "AAA="
    assert (
        _post(client, side, body, secret=wrong).status_code == HTTPStatus.UNAUTHORIZED
    )
    assert _post(client, side, body, age_s=3600).status_code == HTTPStatus.UNAUTHORIZED
    assert chats.fan_messages(side.platform, side.model, since=0) == []


def test_a_delivery_for_someone_elses_model_is_answered_and_not_kept(client, side):
    body = side.mass_for_someone_else("mass-1")

    assert _post(client, side, body).status_code == HTTPStatus.OK
    assert _popups() == []


def test_a_signed_delivery_that_cannot_be_understood_is_not_retried(client, side):
    body = side.message_without_its_model("ann", side.new_id(), time.time())

    assert _post(client, side, body).status_code == HTTPStatus.OK
    assert chats.fan_messages(side.platform, side.model, since=0) == []


def test_a_name_postgres_refuses_does_not_fail_the_delivery(client, side):
    name = "a\x00b" + "x" * 300
    body = side.message_from_name(name, side.new_id(), time.time())

    assert _post(client, side, body).status_code == HTTPStatus.OK
    [message] = chats.fan_messages(side.platform, side.model, since=0)
    assert "\x00" not in message.fan_name
    assert len(message.fan_name) == 255


# --- the emoji --------------------------------------------------------------


def test_the_models_emoji_on_a_fans_message_answers_it(client, side):
    now = time.time()
    message_id = side.fan_writes("ann", now - LATE)
    sync(side.source)
    assert _popups() == [f"Chats unanswered (1): @{side.model}"]

    side.react("ann", message_id, now - MIN)
    side.notice_reaction(client, "ann", message_id, now - MIN)

    assert _popups() == []
    [message] = chats.fan_messages(side.platform, side.model, since=0)
    assert round(message.replied_at) == round(now - MIN)


def test_the_fans_own_emoji_answers_nothing(client, side):
    now = time.time()
    message_id = side.fan_writes("ann", now - LATE)
    sync(side.source)

    side.react("ann", message_id, now - MIN, by_model=False)
    side.notice_reaction(client, "ann", message_id, now - MIN)

    assert _popups() == [f"Chats unanswered (1): @{side.model}"]


# --- the sync ---------------------------------------------------------------


def test_the_sync_alone_fills_the_popup_and_clears_it(side):
    now = time.time()
    side.fan_writes("ann", now - LATE)
    side.fan_writes("bob", now - LATE)
    sync(side.source)
    assert _popups() == [f"Chats unanswered (2): @{side.model}"]

    side.model_replies("ann", now - MIN)
    sync(side.source)
    assert _popups() == [f"Chats unanswered (1): @{side.model}"]


def test_a_webhook_and_a_sync_seeing_one_message_count_it_once(client, side):
    now = time.time()
    message_id = side.fan_writes("ann", now - LATE)
    body = side.message("ann", message_id, from_fan=True, at=now - LATE)

    assert _post(client, side, body).status_code == HTTPStatus.OK
    sync(side.source)

    assert len(chats.fan_messages(side.platform, side.model, since=0)) == 1


def test_a_platform_answering_500_loses_nothing(side):
    now = time.time()
    side.fan_writes("ann", now - LATE)
    side.fail_next_reads(100)

    with pytest.raises(ChatSourceError):
        run_when_due(side.platform)
    side.fake.fail_next.clear()

    run_when_due(side.platform)
    assert _popups() == [f"Chats unanswered (1): @{side.model}"]


def test_a_failed_full_round_is_tried_again_next_minute(side):
    side.fail_next_reads(100)
    with pytest.raises(ChatSourceError):
        run_when_due(side.platform)
    assert get_text(sync_done_key(side.platform)) is None

    side.fake.fail_next.clear()
    run_when_due(side.platform)
    assert get_text(sync_done_key(side.platform)) == "1"


def test_a_model_with_nothing_delivered_is_read_every_minute(side):
    run_when_due(side.platform)
    side.fan_writes("ann", time.time() - LATE)

    run_when_due(side.platform)

    assert _popups() == [f"Chats unanswered (1): @{side.model}"]


def test_a_delivered_reply_lets_the_sync_rest_but_a_waiting_fan_does_not(
    client, side, monkeypatch
):
    monkeypatch.setenv("OFM_CHATTING_WEBHOOK_BASE_URL", "https://example.invalid/")
    url = f"https://example.invalid/events/{side.platform.value}"
    for hook in chats.webhooks(side.platform):
        chats.save_webhook(side.platform, hook.model_copy(update={"url": url}))
    run_when_due(side.platform)
    assert models_delivering(side.platform) == set()

    _fan_waits(client, side, "ann", ago=MIN)
    assert models_delivering(side.platform) == set()

    _model_replies(client, side, "ann")
    assert models_delivering(side.platform) == {side.model}

    # Delivering, and a fan past the limit: read anyway, or an emoji or a reply no
    # webhook told of would keep them reported for an hour.
    before = side.chat_reads()
    run_when_due(side.platform)
    assert side.chat_reads() == before

    _fan_waits(client, side, "bob", ago=LATE)
    run_when_due(side.platform)
    assert side.chat_reads() > before


# --- destinations -----------------------------------------------------------


def test_a_second_round_of_destinations_changes_nothing(side):
    ensure_destinations()
    first = chats.webhooks(side.platform)

    ensure_destinations()

    assert chats.webhooks(side.platform) == first


# --- the pages --------------------------------------------------------------


def test_the_pages_show_the_late_fan_and_the_average(client, side):
    sign_in_admin(client)
    now = time.time()
    side.fan_writes("ann", now - LATE)
    side.fan_writes("bob", now - LATE)
    side.model_replies("bob", now - LATE + 2 * MIN)
    sync(side.source)

    platforms = client.get("/chatting")
    assert platforms.status_code == HTTPStatus.OK
    assert side.platform.label in platforms.text

    models = client.get(f"/chatting/{side.platform.value}")
    assert models.status_code == HTTPStatus.OK
    assert side.model in models.text

    page = client.get(f"/chatting/{side.platform.value}/{side.model}")
    assert page.status_code == HTTPStatus.OK
    assert "Average response time" in page.text
    assert "2 min" in page.text
    assert "No reply yet" in page.text


def test_a_user_who_is_not_admin_gets_no_chats(client, side):
    _fan_waits(client, side, "ann")
    sign_in(client)

    assert client.get("/chatting/alerts").status_code == HTTPStatus.FORBIDDEN
    page = client.get(f"/chatting/{side.platform.value}/{side.model}")
    assert page.status_code == HTTPStatus.FORBIDDEN


def test_nobody_signed_in_gets_no_chats_but_deliveries_still_arrive(client, side):
    assert (
        client.get("/chatting/alerts", follow_redirects=False).status_code
        != HTTPStatus.OK
    )
    assert _post(client, side, side.mass("mass-1")).status_code == HTTPStatus.OK


def test_a_platform_that_is_not_set_up_answers_404(client):
    reply = client.post("/events/onlyfans", content=b"{}")
    assert reply.status_code == HTTPStatus.NOT_FOUND


def test_the_old_address_is_gone(client):
    reply = client.post("/webhooks/fanvue", content=b"{}", follow_redirects=False)
    assert reply.status_code != HTTPStatus.OK
