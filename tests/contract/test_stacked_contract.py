"""Stacked's API as the client reads it: on the fake, and on the real accounts
(`STACKED_API_KEY_<NAME>` in .env; read-only calls, free)."""

from datetime import UTC, datetime, timedelta

import pytest

from ofmhelpers.config import settings
from ofmhelpers.integrations.stacked import (
    AccountType,
    StackedAuthError,
    StackedClient,
    StackedForbiddenError,
)
from ofmhelpers.integrations.stacked.fake import (
    AGENCY_KEY,
    CREATOR,
    CREATOR_KEY,
    fake_stacked,
)

_FAKE_CHATS = 3
_NOBODY = "user_no_such_creator"


def _key_of_kind(backend: str, kind: AccountType) -> str:
    """A real key of that kind from .env; the test is skipped without one."""
    if backend == "fake":
        return CREATOR_KEY if kind == AccountType.CREATOR else AGENCY_KEY
    for key in (k.key for k in settings.stacked.api_keys):
        if StackedClient(key).me().account.type == kind:
            return key
    pytest.skip(f"no {kind} key in .env (STACKED_API_KEY_<NAME>)")


@pytest.fixture
def creator_key(backend) -> str:
    if backend == "fake":
        fake = fake_stacked()
        for n in range(_FAKE_CHATS):
            fake.add_chat(CREATOR, f"fan_{n}", f"channel_{n}")
            fake.add_message(f"channel_{n}", f"fan_{n}", "hi")
    return _key_of_kind(backend, AccountType.CREATOR)


def test_a_creator_key_reaches_only_its_creator(creator_key):
    client = StackedClient(creator_key)

    me = client.me()

    assert me.account.type == AccountType.CREATOR
    assert [c.user_id for c in client.creators()] == [me.account.id]


def test_an_agency_key_reaches_its_linked_creators(backend):
    client = StackedClient(_key_of_kind(backend, AccountType.AGENCY))

    assert client.me().account.type == AccountType.AGENCY
    assert client.creators()


def test_chats_come_latest_message_first_with_their_members(creator_key):
    client = StackedClient(creator_key)
    creator = client.me().account.id

    chats = list(client.chats(creator))

    assert chats
    assert all(creator in chat.member_user_ids for chat in chats)
    sent = [c.last_message.created_at for c in chats if c.last_message]
    assert sent == sorted(sent, reverse=True)


def test_a_chats_messages_come_newest_first(creator_key):
    client = StackedClient(creator_key)
    creator = client.me().account.id
    chat = next(c for c in client.chats(creator) if c.last_message)

    messages = client.messages(creator, chat.channel_url)

    sent = [m.created_at for m in messages]
    assert sent
    assert sent == sorted(sent, reverse=True)


def test_a_wrong_key_is_refused(backend):
    assert backend
    with pytest.raises(StackedAuthError):
        StackedClient("stk_live_wrong").me()


def test_a_creator_the_key_cannot_reach_is_refused(creator_key):
    with pytest.raises(StackedForbiddenError):
        next(StackedClient(creator_key).chats(_NOBODY))


def test_insights_answer_for_any_window(creator_key):
    """The daily report's subscribers and sales (scope `insights:read`)."""
    client = StackedClient(creator_key)
    creator = client.me().account.id
    end = datetime.now(UTC)
    start = end - timedelta(days=1)

    sold = client.earnings(creator, start, end)
    subs = client.subscriber_summary(creator, start, end)

    assert sold is not None
    assert sold.currency
    assert sold.gross_cents >= sold.net_cents >= 0
    assert subs is not None
    assert min(subs.new, subs.returning, subs.canceled, subs.active) >= 0
