# Copyright (C) 2026 jpkrajewski

import time

from pydantic import BaseModel

from ofmhelpers.cache import get_text, set_text
from ofmhelpers.config import settings
from ofmhelpers.log import get_logger
lazy from ofmhelpers.integrations.senders.telegram.client import TelegramClient
lazy from ofmhelpers.integrations.senders.telegram.models import Message

logger = get_logger(__name__)

_OFFSET = "telegram:updates:offset"
_OFFSET_TTL_S = 7 * 86400


class Ask(BaseModel):
    chat_id: str
    word: str


class Start(BaseModel):
    chat_id: str
    token: str


class Inbox(BaseModel):
    asks: list[Ask]
    starts: list[Start] = []
    next_offset: int | None = None


def word_of(text: str, bot_name: str) -> str:
    first = text.strip().split(maxsplit=1)[0] if text.strip() else ""
    word, _, to = first.removeprefix("/").partition("@")
    if to and to.casefold() != bot_name.casefold():
        return ""
    return word.casefold()


def _ask(
    message: Message,
    words: set[str],
    allowed: set[str],
    bot_name: str,
) -> Ask | None:
    word = word_of(message.text, bot_name)
    if word not in words:
        return None
    chat = str(message.chat.id)
    if chat not in allowed:
        logger.info(
            "telegram: %r typed in chat %s (%s), not in OFM_TELEGRAM_CHAT_IDS",
            word,
            chat,
            message.chat.type,
        )
        return None
    if message.date < time.time() - settings.telegram.ask_max_age_s:
        logger.info("telegram: %r is too old to answer", word)
        return None
    return Ask(chat_id=chat, word=word)


def _start(message: Message) -> Start | None:
    command, _, token = message.text.strip().partition(" ")
    if message.chat.type != "private" or command != "/start" or not token.strip():
        return None
    return Start(chat_id=str(message.chat.id), token=token.strip())


def read(client: TelegramClient, words: set[str], bot_name: str) -> Inbox:
    s = settings.telegram
    saved = get_text(_OFFSET)
    updates = client.updates(int(saved) if saved else None, s.ask_wait_s)
    if not updates:
        return Inbox(asks=[])
    allowed = set(s.chat_id_list())
    asks, starts = [], []
    for update in updates:
        message = update.message or update.channel_post
        if message is None:
            continue
        if (start := _start(message)) is not None:
            starts.append(start)
        elif (found := _ask(message, words, allowed, bot_name)) is not None:
            asks.append(found)
    return Inbox(
        asks=asks,
        starts=starts,
        next_offset=max(u.update_id for u in updates) + 1,
    )


def confirm(inbox: Inbox) -> None:
    if inbox.next_offset is not None:
        set_text(_OFFSET, str(inbox.next_offset), _OFFSET_TTL_S)
