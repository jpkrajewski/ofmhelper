"""Telegram: the app's bot posts events to its chats. See ../CLAUDE.md."""

from ofmhelpers.integrations.senders.telegram.client import (
    TelegramClient,
    TelegramError,
)
from ofmhelpers.integrations.senders.telegram.sender import (
    TelegramSender,
    message_text,
)

__all__ = ["TelegramClient", "TelegramError", "TelegramSender", "message_text"]
