# Copyright (C) 2026 jpkrajewski

import logging

from ofmhelpers.config import settings
from ofmhelpers.integrations.senders.telegram.client import (
    TelegramClient,
    TelegramError,
    TelegramMovedError,
    telegram_client,
)
from ofmhelpers.integrations.senders.telegram.models import MAX_TEXT
lazy from ofmhelpers.integrations.senders.schema import Event

logger = logging.getLogger(__name__)

_CUT = "…"
_GAP = "\n\n"
_MAX_TITLE = MAX_TEXT // 4
_MAX_LINK = MAX_TEXT // 2


def _units(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _cut(text: str, room: int) -> str:
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
        s = settings.telegram
        return s.group_pace_s if target.startswith("-") else s.chat_pace_s

    def send(self, target: str, event: Event) -> str:
        client = _client()
        text = message_text(event)
        try:
            sent = client.send_message(target, text)
        except TelegramMovedError as moved:
            logger.exception(
                "telegram: a group in OFM_TELEGRAM_CHAT_IDS became a supergroup:"
                " put %s there in its place",
                moved.chat_id,
            )
            sent = client.send_message(moved.chat_id, text)
        return f"{sent.chat.id}:{sent.message_id}"

    def delete(self, target: str, ref: str) -> None:  # noqa: ARG002
        chat_id, message_id = ref.rsplit(":", 1)
        _client().delete_message(chat_id, int(message_id))


def _client() -> TelegramClient:
    token = settings.telegram.bot_token
    if token is None:
        msg = "OFM_TELEGRAM_BOT_TOKEN is not set"
        raise TelegramError(msg, permanent=True)
    return telegram_client(token.get_secret_value())
