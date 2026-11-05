"""The Stacked client on its fake: one client for a creator key and an agency
key, paging, retries, refusals."""

from datetime import UTC, datetime

import pytest

from ofmhelpers.config import settings
from ofmhelpers.integrations.stacked import (
    AccountType,
    StackedAuthError,
    StackedClient,
    StackedError,
    StackedForbiddenError,
    reachable,
)
from ofmhelpers.integrations.stacked import client as stacked_client
from ofmhelpers.integrations.stacked.fake import (
    AGENCY,
    AGENCY_KEY,
    CREATOR,
    CREATOR_KEY,
    OTHER_CREATOR,
    fake_stacked,
)
from ofmhelpers.utils.outbound import FakeReply

pytestmark = pytest.mark.usefixtures("fake_externals")

_WRONG_KEY = "stk_live_wrong"
_SERVER_DOWN = FakeReply.json_body({"error": "upstream"}, 502)


@pytest.fixture(autouse=True)
def no_waits(monkeypatch):
    monkeypatch.setattr(stacked_client.time, "sleep", lambda _s: None)


def test_a_creator_key_acts_on_its_one_creator():
    client = StackedClient(CREATOR_KEY)

    me = client.me()

    assert me.account.type == AccountType.CREATOR
    assert me.account.id == CREATOR
    assert [c.user_id for c in client.creators()] == [CREATOR]


def test_an_agency_key_acts_on_every_linked_creator():
    client = StackedClient(AGENCY_KEY)

    me = client.me()

    assert me.account.type == AccountType.AGENCY
    assert me.account.id == AGENCY
    assert [c.user_id for c in client.creators()] == [CREATOR, OTHER_CREATOR]


def test_an_agency_key_reads_each_creators_own_chats():
    fake = fake_stacked()
    fake.add_chat(CREATOR, "fan_a", "channel_a")
    fake.add_chat(OTHER_CREATOR, "fan_b", "channel_b")
    client = StackedClient(AGENCY_KEY)

    assert [c.channel_url for c in client.chats(CREATOR)] == ["channel_a"]
    assert [c.channel_url for c in client.chats(OTHER_CREATOR)] == ["channel_b"]


def test_a_creator_key_cannot_read_another_creator():
    with pytest.raises(StackedForbiddenError, match="does not have access"):
        list(StackedClient(CREATOR_KEY).chats(OTHER_CREATOR))


def test_a_wrong_key_is_refused_and_never_shown():
    with pytest.raises(StackedAuthError, match="Wrong Stacked API key") as refused:
        StackedClient(_WRONG_KEY).me()

    assert _WRONG_KEY not in str(refused.value)


def test_reachable_gives_a_creator_two_keys_reach_once():
    pairs = reachable([CREATOR_KEY, AGENCY_KEY])

    assert [creator.user_id for _, creator in pairs] == [CREATOR, OTHER_CREATOR]
    assert pairs[0][0].key_hint == StackedClient(CREATOR_KEY).key_hint
    assert pairs[1][0].key_hint == StackedClient(AGENCY_KEY).key_hint


def test_reachable_skips_a_dead_key_and_keeps_the_others():
    pairs = reachable([_WRONG_KEY, CREATOR_KEY])

    assert [creator.user_id for _, creator in pairs] == [CREATOR]


def test_chats_page_to_the_end_with_the_latest_message_first():
    fake = fake_stacked()
    total = settings.stacked.page_size + 3
    for n in range(total):
        fake.add_chat(CREATOR, f"fan_{n}", f"channel_{n}")
        fake.add_message(f"channel_{n}", f"fan_{n}", "hi")

    chats = list(StackedClient(CREATOR_KEY).chats(CREATOR))

    assert len(chats) == total
    assert chats[0].channel_url == f"channel_{total - 1}"
    assert chats[-1].channel_url == "channel_0"
    assert fake.calls.count(("GET", "/external-service/v1/chats")) == 2


def test_stopping_early_fetches_no_further_page():
    fake = fake_stacked()
    for n in range(settings.stacked.page_size + 3):
        fake.add_chat(CREATOR, f"fan_{n}", f"channel_{n}")

    first = next(StackedClient(CREATOR_KEY).chats(CREATOR))

    assert first.channel_url
    assert fake.calls.count(("GET", "/external-service/v1/chats")) == 1


def test_a_chat_nobody_wrote_in_has_no_last_message():
    fake_stacked().add_chat(CREATOR, "fan", "channel")

    chat = next(StackedClient(CREATOR_KEY).chats(CREATOR))

    assert chat.last_message is None


def test_message_times_arrive_as_utc_datetimes():
    fake = fake_stacked()
    fake.add_chat(CREATOR, "fan", "channel")
    fake.add_message("channel", "fan", "hi", at_ms=1_787_756_645_000)

    chat = next(StackedClient(CREATOR_KEY).chats(CREATOR))

    assert chat.last_message is not None
    assert chat.last_message.created_at == datetime(2026, 8, 26, 15, 4, 5, tzinfo=UTC)


def test_messages_come_newest_first_and_page_backwards():
    fake = fake_stacked()
    fake.add_chat(CREATOR, "fan", "channel")
    oldest = fake.add_message("channel", "fan", "one")
    middle = fake.add_message("channel", CREATOR, "two")
    newest = fake.add_message("channel", "fan", "three")
    client = StackedClient(CREATOR_KEY)

    assert [m.message_id for m in client.messages(CREATOR, "channel")] == [
        newest["messageId"],
        middle["messageId"],
        oldest["messageId"],
    ]
    assert [
        m.message_id
        for m in client.messages(
            CREATOR, "channel", before_message_id=newest["messageId"]
        )
    ] == [middle["messageId"], oldest["messageId"]]


def test_the_providers_own_events_have_no_sender():
    fake = fake_stacked()
    fake.add_chat(CREATOR, "fan", "channel")
    fake.add_message("channel", "", "")

    event = StackedClient(CREATOR_KEY).messages(CREATOR, "channel")[0]

    assert event.user_id == ""
    assert event.type == "ADMM"


def test_a_missing_chat_is_an_error_worth_showing():
    with pytest.raises(StackedError, match="Stacked error 404: failed to fetch chat"):
        StackedClient(CREATOR_KEY).messages(CREATOR, "no_such_channel")


def test_a_rate_limit_is_waited_out_as_asked(monkeypatch):
    waits: list[float] = []
    monkeypatch.setattr(stacked_client.time, "sleep", waits.append)
    fake_stacked().fail_next = [
        FakeReply(status=429, body=b'{"error": "slow"}', headers={"Retry-After": "7"})
    ]

    assert StackedClient(CREATOR_KEY).me().account.id == CREATOR
    assert waits == [7.0]


def test_a_passing_server_error_is_retried():
    fake_stacked().fail_next = [_SERVER_DOWN, _SERVER_DOWN]

    assert StackedClient(CREATOR_KEY).me().account.id == CREATOR


def test_a_lasting_server_error_gives_up_with_a_clear_message():
    fake = fake_stacked()
    fake.fail_next = [_SERVER_DOWN] * settings.stacked.tries

    with pytest.raises(StackedError, match="Stacked error 502: upstream"):
        StackedClient(CREATOR_KEY).me()

    assert len(fake.calls) == settings.stacked.tries


def test_a_refusal_is_not_retried():
    fake = fake_stacked()

    with pytest.raises(StackedAuthError):
        StackedClient(_WRONG_KEY).me()

    assert len(fake.calls) == 1


def test_a_new_env_line_adds_a_key_of_either_kind(monkeypatch):
    monkeypatch.setenv("STACKED_API_KEY_FIRST", CREATOR_KEY)
    monkeypatch.setenv("STACKED_API_KEY_MY_AGENCY", AGENCY_KEY)
    monkeypatch.setenv("STACKED_API_KEY_EMPTY", "")

    assert settings.stacked.api_keys == {"first": CREATOR_KEY, "my_agency": AGENCY_KEY}
