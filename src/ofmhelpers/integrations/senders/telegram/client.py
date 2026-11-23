# Copyright (C) 2026 jpkrajewski

import os
from functools import lru_cache
from http import HTTPStatus
from typing import Any

import requests
from pydantic import ValidationError

from ofmhelpers.config import settings
from ofmhelpers.integrations import outbound
from ofmhelpers.integrations.senders.base import SendError
from ofmhelpers.integrations.senders.telegram.models import (
    Bot,
    Chat,
    Message,
    Reply,
    Update,
)
from ofmhelpers.log import get_logger

logger = get_logger(__name__)


class TelegramError(SendError): ...


class TelegramMovedError(TelegramError):
    def __init__(self, message: str, chat_id: str) -> None:
        super().__init__(message, permanent=True)
        self.chat_id = chat_id


class TelegramClient:
    def __init__(self, token: str) -> None:
        self._base = f"{settings.telegram.api_url.rstrip('/')}/bot{token}"
        self._session = outbound.mount(requests.Session())

    def me(self) -> Bot:
        return Bot.model_validate(self._call("getMe", {}).result)

    def send_message(self, chat_id: str, text: str) -> Message:
        return Message.model_validate(
            self._call(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": text,
                    "link_preview_options": {"is_disabled": True},
                },
            ).result,
        )

    def delete_message(self, chat_id: str, message_id: int) -> None:
        self._call("deleteMessage", {"chat_id": chat_id, "message_id": message_id})

    def chats(self) -> list[Chat]:
        updates = [
            Update.model_validate(u) for u in self._call("getUpdates", {}).result
        ]
        found = {u.chat.id: u.chat for u in updates if u.chat is not None}
        return list(found.values())

    def updates(self, offset: int | None, wait_s: float) -> list[Update]:
        payload: dict[str, Any] = {
            "timeout": int(wait_s),
            "allowed_updates": ["message", "channel_post"],
        }
        if offset is not None:
            payload["offset"] = offset
        found = self._call("getUpdates", payload, extra_wait_s=wait_s)
        return [read for raw in found.result if (read := _update(raw)) is not None]

    def _call(
        self,
        method: str,
        payload: dict[str, Any],
        extra_wait_s: float = 0,
    ) -> Reply:
        try:
            response = self._session.post(
                f"{self._base}/{method}",
                json=payload,
                timeout=settings.telegram.timeout_s + extra_wait_s,
            )
        except requests.RequestException as exc:
            msg = f"Telegram {method} failed: {type(exc).__name__}"
            raise TelegramError(msg) from None
        try:
            reply = Reply.model_validate_json(response.content)
        except ValidationError:
            msg = f"Telegram {method} answered {response.status_code}, not JSON"
            raise TelegramError(msg) from None
        if not reply.ok:
            raise _refusal(method, reply)
        return reply


def _update(raw: Any) -> Update | None:
    try:
        return Update.model_validate(raw)
    except ValidationError:  # silent: the error quotes someone's words
        update_id = raw.get("update_id") if isinstance(raw, dict) else None
        if not isinstance(update_id, int):
            return None
        logger.warning("telegram update %s unreadable, skipped", update_id)
        return Update(update_id=update_id)


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
        msg,
        permanent=permanent and code != HTTPStatus.TOO_MANY_REQUESTS,
    )


def telegram_client(token: str) -> TelegramClient:
    return _client(token, os.getpid())


@lru_cache(maxsize=4)
def _client(token: str, pid: int) -> TelegramClient:  # noqa: ARG001
    return TelegramClient(token)


@lru_cache(maxsize=2)
def bot_name(token: str) -> str:
    return telegram_client(token).me().username
