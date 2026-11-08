"""What people type to the bot: one word asks for something (`money`).

`read(words)` waits for new messages (getUpdates, long polling) and keeps the
asks: a message whose first word is one of `words` (`money`, `Money`, `/money`,
`/money@bot`), sent in a chat of `OFM_TELEGRAM_CHAT_IDS` (nobody else is
answered: the numbers are the agency's), no older than `ask_max_age_s` (a server
that was down does not answer what was typed an hour ago). Anything else is
dropped; an ask from another chat is logged with that chat's id, which is how a
new chat id is found once a server reads the bot (`TelegramClient.chats` then
finds nothing: the updates are taken).

Where to read from is kept in Redis (`_OFFSET`); `confirm` moves it past what was
read, only once the asks are handled. Telegram keeps an unread update 24 h.

In a group, Telegram hands a bot only commands (`/money`) unless its privacy
mode is off (@BotFather, /setprivacy, Disable, then add the bot again).
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from pydantic import BaseModel

from ofmhelpers.cache import get_text, set_text
from ofmhelpers.config import settings
from ofmhelpers.log import get_logger

if TYPE_CHECKING:
    from ofmhelpers.integrations.senders.telegram.client import TelegramClient
    from ofmhelpers.integrations.senders.telegram.models import Message

logger = get_logger(__name__)

_OFFSET = "telegram:updates:offset"
_OFFSET_TTL_S = 7 * 86400


class Ask(BaseModel):
    chat_id: str
    word: str


class Inbox(BaseModel):
    asks: list[Ask]
    # Where the next read starts; None: nothing was read.
    next_offset: int | None = None


def word_of(text: str, bot_name: str) -> str:
    """The ask in a message: its first word, any case, without the command's
    `/` and `@<this bot>` (`/money@ofm_bot` = `money`); a command for another
    bot (`/money@other_bot`) asks nothing."""
    first = text.strip().split(maxsplit=1)[0] if text.strip() else ""
    word, _, to = first.removeprefix("/").partition("@")
    if to and to.casefold() != bot_name.casefold():
        return ""
    return word.casefold()


def _ask(
    message: Message, words: set[str], allowed: set[str], bot_name: str
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


def read(client: TelegramClient, words: set[str], bot_name: str) -> Inbox:
    """The asks typed since the last `confirm`, waiting up to `ask_wait_s` for
    one. `bot_name`: this bot's username (`/money@<it>`)."""
    s = settings.telegram
    saved = get_text(_OFFSET)
    updates = client.updates(int(saved) if saved else None, s.ask_wait_s)
    if not updates:
        return Inbox(asks=[])
    allowed = set(s.chat_id_list())
    asks = []
    for update in updates:
        message = update.message or update.channel_post
        found = _ask(message, words, allowed, bot_name) if message is not None else None
        if found is not None:
            asks.append(found)
    return Inbox(asks=asks, next_offset=max(u.update_id for u in updates) + 1)


def confirm(inbox: Inbox) -> None:
    """The next read starts after what `inbox` read."""
    if inbox.next_offset is not None:
        set_text(_OFFSET, str(inbox.next_offset), _OFFSET_TTL_S)
