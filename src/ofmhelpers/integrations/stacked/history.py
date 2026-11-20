lazy from collections.abc import Collection, Iterator, Mapping
lazy from datetime import datetime

from ofmhelpers.config import settings
from ofmhelpers.db.chats.schema import ChatEvent, ChatRead
from ofmhelpers.integrations.stacked.errors import StackedError
from ofmhelpers.log import get_logger
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
    if message.is_removed or not message.user_id:
        return None
    kind = message.custom_type or message.type or "attachment"
    return ChatEvent(
        message_id=str(message.message_id),
        from_fan=message.user_id != creator_user_id,
        at=message.created_at.timestamp(),
        text=(message.text or f"[{kind.lower()}]").replace("\x00", ""),
    )


def _reaction_reply(messages: list[Message], creator_user_id: str) -> ChatEvent | None:
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
