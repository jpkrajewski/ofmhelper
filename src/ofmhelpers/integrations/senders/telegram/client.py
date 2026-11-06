"""The Bot API calls this app makes: who the bot is, the chats that wrote to it,
a message to a chat. Through `utils.outbound`, so OFM_FAKE_EXTERNALS answers it
from `telegram/fake.py`.

The token is part of every URL (`/bot<token>/<method>`): an error here never
quotes the request, and `redact.py` masks the token shape in any span or log
that does.

How a refusal is told apart (Bot API "Making requests"): 429 carries
`parameters.retry_after` (flood control); a group that became a supergroup
answers with `parameters.migrate_to_chat_id`; any other 4xx (chat not found, bot
removed or blocked) cannot succeed on a retry; 5xx and network errors can."""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

import requests
from pydantic import ValidationError

from ofmhelpers.config import settings
from ofmhelpers.integrations.senders.base import SendError
from ofmhelpers.integrations.senders.telegram.models import (
    Bot,
    Chat,
    Message,
    Reply,
    Update,
)
from ofmhelpers.utils import outbound


class TelegramError(SendError):
    """The Bot API refused or failed; never holds the token."""


class TelegramMovedError(TelegramError):
    """The group became a supergroup: it is now `chat_id`."""

    def __init__(self, message: str, chat_id: str) -> None:
        super().__init__(message, permanent=True)
        self.chat_id = chat_id


class TelegramClient:
    """One bot (its token)."""

    def __init__(self, token: str) -> None:
        self._base = f"{settings.telegram.api_url.rstrip('/')}/bot{token}"

    def me(self) -> Bot:
        return Bot.model_validate(self._call("getMe", {}))

    def send_message(self, chat_id: str, text: str) -> Message:
        """Plain text (no parse mode: nothing in it is markup), no link preview."""
        return Message.model_validate(
            self._call(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": text,
                    "link_preview_options": {"is_disabled": True},
                },
            )
        )

    def delete_message(self, chat_id: str, message_id: int) -> None:
        """A message the bot sent (Telegram: under 48 h old)."""
        self._call("deleteMessage", {"chat_id": chat_id, "message_id": message_id})

    def chats(self) -> list[Chat]:
        """The chats that wrote to the bot or added it in the last 24 h (what
        Telegram keeps): where `OFM_TELEGRAM_CHAT_IDS` comes from."""
        updates = [Update.model_validate(u) for u in self._call("getUpdates", {})]
        found = {u.chat.id: u.chat for u in updates if u.chat is not None}
        return list(found.values())

    def _call(self, method: str, payload: dict[str, Any]) -> Any:
        try:
            with outbound.mount(requests.Session()) as session:
                response = session.post(
                    f"{self._base}/{method}",
                    json=payload,
                    timeout=settings.telegram.timeout_s,
                )
        except requests.RequestException as exc:
            # str(exc) quotes the URL, and the URL holds the token.
            msg = f"Telegram {method} failed: {type(exc).__name__}"
            raise TelegramError(msg) from None
        try:
            reply = Reply.model_validate_json(response.content)
        except ValidationError:
            msg = f"Telegram {method} answered {response.status_code}, not JSON"
            raise TelegramError(msg) from None
        if not reply.ok:
            raise _refusal(method, reply)
        return reply.result


def _refusal(method: str, reply: Reply) -> TelegramError:
    msg = f"Telegram {method} refused ({reply.error_code}): {reply.description}"
    params = reply.parameters
    if params is not None and params.migrate_to_chat_id is not None:
        return TelegramMovedError(msg, str(params.migrate_to_chat_id))
    if params is not None and params.retry_after is not None:
        return TelegramError(msg, retry_after=params.retry_after)
    code = reply.error_code or 0
    permanent = HTTPStatus.BAD_REQUEST <= code < HTTPStatus.INTERNAL_SERVER_ERROR
    return TelegramError(
        msg, permanent=permanent and code != HTTPStatus.TOO_MANY_REQUESTS
    )
