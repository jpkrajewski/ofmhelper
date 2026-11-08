"""Events as Telegram messages from the app's bot, one chat per target
(`OFM_TELEGRAM_CHAT_IDS`)."""

from ofmhelpers.config import settings
from ofmhelpers.integrations.senders.telegram.client import (
    TelegramClient,
    TelegramError,
    TelegramMovedError,
)
from ofmhelpers.integrations.senders.telegram.models import MAX_TEXT
from ofmhelpers.log import get_logger
lazy from ofmhelpers.schemas.events import Event

logger = get_logger(__name__)

_CUT = "…"
_GAP = "\n\n"
# A title or a link longer than this is not worth a message's room.
_MAX_TITLE = MAX_TEXT // 4
_MAX_LINK = MAX_TEXT // 2


def _units(text: str) -> int:
    """Length as Telegram counts it: UTF-16 code units (an emoji is two)."""
    return len(text.encode("utf-16-le")) // 2


def _cut(text: str, room: int) -> str:
    """`text` in at most `room` units, ending in … when cut."""
    if _units(text) <= room:
        return text
    kept: list[str] = []
    used = 0
    for char in text:
        size = _units(char)
        if used + size > room - 1:
            break
        kept.append(char)
        used += size
    return "".join(kept) + _CUT


def message_text(event: Event) -> str:
    """Title, text, link, a blank line between each (the owner's layout,
    2026-11-06), within Telegram's limit: only the text is cut to make room. A
    link that is not a full http(s) address is left out (a bare `/links` would
    read as a bot command)."""
    url = event.url or ""
    link = url if url.startswith(("https://", "http://")) else ""
    if _units(link) > _MAX_LINK:
        link = ""
    title = _cut(event.title, _MAX_TITLE)
    room = MAX_TEXT - _units(title) - _units(link) - 2 * len(_GAP)
    body = _cut(event.text, room) if event.text and room > 1 else ""
    return _GAP.join(part for part in (title, body, link) if part)


class TelegramSender:
    name = "telegram"

    def targets(self) -> list[str]:
        s = settings.telegram
        return s.chat_id_list() if s.bot_token is not None else []

    def pace_s(self, target: str) -> float:
        """A group takes about 20 messages a minute, a private chat about one a
        second (Bot API FAQ); a group's id is negative."""
        s = settings.telegram
        return s.group_pace_s if target.startswith("-") else s.chat_pace_s

    def send(self, target: str, event: Event) -> str:
        """The message as `<chat id>:<message id>`."""
        client = _client()
        text = message_text(event)
        try:
            sent = client.send_message(target, text)
        except TelegramMovedError as moved:
            # Sent to the new id this time; the setting still names the old one.
            logger.exception(
                "telegram: a group in OFM_TELEGRAM_CHAT_IDS became a supergroup:"
                " put %s there in its place",
                moved.chat_id,
            )
            sent = client.send_message(moved.chat_id, text)
        return f"{sent.chat.id}:{sent.message_id}"

    def delete(self, target: str, ref: str) -> None:  # noqa: ARG002 -- ref names its chat
        chat_id, message_id = ref.rsplit(":", 1)
        _client().delete_message(chat_id, int(message_id))


def _client() -> TelegramClient:
    token = settings.telegram.bot_token
    if token is None:
        msg = "OFM_TELEGRAM_BOT_TOKEN is not set"
        raise TelegramError(msg, permanent=True)
    return TelegramClient(token.get_secret_value())
