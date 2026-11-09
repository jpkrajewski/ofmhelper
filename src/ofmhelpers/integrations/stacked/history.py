"""A creator's new messages, chat by chat: the sync behind /chatting's history,
which catches what webhooks missed and is all there is without them.

The chat list carries each chat's last message, so only a chat whose last message
changed since the last read is opened. That is one call per changed chat: a mass
message changes most chats at once, so that one round takes minutes (one call at
a time stays well under the rate limit). The whole list is read every round: a fan
who is still waiting, however long ago they wrote, may have blocked or left.

A model's emoji reaction on a fan's message answers it. It changes no last message,
the list does not show it and no webhook announces it, so a chat with a fan waiting
has its newest message read every round too.
"""

lazy from collections.abc import Collection, Iterator, Mapping
lazy from datetime import datetime

from ofmhelpers.config import settings
from ofmhelpers.integrations.stacked.errors import StackedError
from ofmhelpers.log import get_logger
from ofmhelpers.schemas.chatting import ChatEvent, ChatRead
lazy from ofmhelpers.integrations.stacked.client import StackedClient
lazy from ofmhelpers.integrations.stacked.models import Message

logger = get_logger(__name__)

_MS_PER_S = 1000


def _new_messages(
    client: StackedClient,
    creator_user_id: str,
    channel_url: str,
    seen: str | None,
    oldest: datetime,
) -> list[Message]:
    """The chat's messages after `seen`, newest first. History stops at `oldest`,
    but a fan's messages nobody has answered yet are read however old: they are
    still waiting."""
    seen_id = int(seen) if seen else 0
    found: list[Message] = []
    before: int | None = None
    answered = False
    while True:
        page = client.messages(creator_user_id, channel_url, before_message_id=before)
        for message in page:
            if message.message_id <= seen_id:
                return found
            if answered and message.created_at < oldest:
                return found
            found.append(message)
            answered = answered or message.user_id == creator_user_id
        if len(page) < settings.stacked.page_size:
            return found
        before = page[-1].message_id


def chat_event(message: Message, creator_user_id: str) -> ChatEvent | None:
    """None for what nobody said: the provider's own events, removed messages.
    Webhooks leave `type` empty, so a message with no text may name nothing."""
    if message.is_removed or not message.user_id:
        return None
    kind = message.custom_type or message.type or "attachment"
    return ChatEvent(
        message_id=str(message.message_id),
        from_fan=message.user_id != creator_user_id,
        at=message.created_at.timestamp(),
        # Postgres refuses a NUL in text, which would stop the chat being saved.
        text=(message.text or f"[{kind.lower()}]").replace("\x00", ""),
    )


def _reaction_reply(messages: list[Message], creator_user_id: str) -> ChatEvent | None:
    """The model's reaction on the fan's newest message (`messages`: newest first), as
    the reply it is, at the moment it was put. One on an older message says nothing
    about what the fan wrote since."""
    newest = next(
        (m for m in messages if m.user_id and m.user_id != creator_user_id), None
    )
    if newest is None:
        return None
    mine = [r for r in newest.reactions if creator_user_id in r.user_ids]
    if not mine:
        return None
    sent = newest.created_at.timestamp()
    put = max((r.updated_at or 0) for r in mine) / _MS_PER_S
    return ChatEvent(
        message_id=f"{newest.message_id}:reaction",
        from_fan=False,
        at=max(sent, put),
        text="",
    )


def new_reads(
    client: StackedClient,
    creator_user_id: str,
    username: str,
    seen: Mapping[str, str],
    oldest: datetime,
    open_channels: Collection[str],
) -> Iterator[ChatRead]:
    """Each chat with something new since `seen` ({channel: message id}), as it
    is read, so a round cut short keeps what it got. A chat whose fan cannot be
    answered comes with `cannot_answer` and is not opened. A chat that cannot be
    read is skipped and read next round, so one bad chat does not stop the ones
    behind it; several in a row mean Stacked is down, and end the round.
    `open_channels`: chats with a fan waiting, whose newest message is read anyway."""
    failed = 0
    for chat in client.chats(creator_user_id):
        last = chat.last_message
        if last is None:
            continue
        channel = chat.channel_url
        fan = chat.answerable_fan(creator_user_id)
        if fan is None:
            yield ChatRead(
                channel_id=channel,
                model_username=username,
                fan_name="",
                last_message_id=str(last.message_id),
                events=[],
                cannot_answer=True,
            )
            continue
        changed = seen.get(channel) != str(last.message_id)
        if not changed and channel not in open_channels:
            continue
        try:
            messages = (
                _new_messages(
                    client, creator_user_id, channel, seen.get(channel), oldest
                )
                if changed
                else client.messages(creator_user_id, channel, limit=1)
            )
        except StackedError:
            logger.warning("stacked chat %s not read", channel, exc_info=True)
            failed += 1
            if failed >= settings.chatting.sync_max_failed_chats:
                raise
            continue
        failed = 0
        events = (
            [chat_event(m, creator_user_id) for m in reversed(messages)]
            if changed
            else []
        )
        reaction = _reaction_reply(messages, creator_user_id)
        if changed or reaction:
            yield ChatRead(
                channel_id=channel,
                model_username=username,
                fan_name=fan.nickname or fan.display_name or fan.user_id,
                last_message_id=str(last.message_id),
                events=[event for event in [*events, reaction] if event is not None],
            )
