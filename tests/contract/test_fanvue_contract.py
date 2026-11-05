"""Fanvue's API as the client reads it, on the fake. The real half is skipped: Fanvue has
no API keys, so it needs an account connected in a browser, and tests never use the dev
database. It was checked by hand on the real agency account, 2026-11-05: see
integrations/fanvue/CLAUDE.md."""

import pytest

from ofmhelpers.integrations.fanvue import FanvueClient
from ofmhelpers.integrations.fanvue.errors import FanvueAuthError, FanvueRefusedError
from ofmhelpers.integrations.fanvue.fake import (
    AGENCY,
    CREATOR,
    CREATOR_HANDLE,
    fake_fanvue,
)
from ofmhelpers.integrations.fanvue.oauth import refresh

_FAN = "fan-uuid"


@pytest.fixture
def client(backend) -> FanvueClient:
    if backend == "real":
        pytest.skip("Fanvue needs an account connected in a browser")
    token = fake_fanvue().new_tokens()["access_token"]
    return FanvueClient(lambda: token)


def test_me_says_who_and_what_kind(client):
    me = client.me()

    assert me.uuid == AGENCY
    assert "agency" in me.roles


def test_an_agency_reaches_its_creators(client):
    assert CREATOR_HANDLE in [c.handle for c in client.managed_creators()]


def test_chats_come_with_their_last_message(client):
    message = fake_fanvue().add_message(CREATOR, _FAN, from_fan=True, text="hi")

    [chat] = list(client.chats(CREATOR))

    assert chat.user.uuid == _FAN
    assert chat.last_message.uuid == message


def test_messages_come_newest_first_and_end_with_no_filter(client):
    first = fake_fanvue().add_message(CREATOR, _FAN, from_fan=True, at=1_790_000_000)
    second = fake_fanvue().add_message(CREATOR, _FAN, from_fan=False, at=1_790_000_100)

    page = client.messages(CREATOR, _FAN)

    assert [m.uuid for m in page.data] == [second, first]
    assert page.date_filter is None


def test_a_refused_chat_is_a_refusal(client):
    fake_fanvue().add_message(CREATOR, _FAN, from_fan=True)

    with pytest.raises(FanvueRefusedError):
        client.messages(CREATOR, "no-such-fan")


def test_a_dead_token_is_an_auth_error(backend):
    if backend == "real":
        pytest.skip("Fanvue needs an account connected in a browser")
    with pytest.raises(FanvueAuthError):
        FanvueClient(lambda: "not-a-token").me()


def test_a_refresh_token_works_once(backend, monkeypatch):
    if backend == "real":
        pytest.skip("Fanvue needs an account connected in a browser")
    monkeypatch.setenv("FANVUE_CLIENT_ID", "fake-client")
    monkeypatch.setenv("FANVUE_CLIENT_SECRET", "fake-secret")
    spent = fake_fanvue().new_tokens()["refresh_token"]

    fresh = refresh(spent)
    assert fresh is not None
    assert fresh.refresh_token != spent

    assert refresh(spent) is None
    assert refresh(fresh.refresh_token) is None
