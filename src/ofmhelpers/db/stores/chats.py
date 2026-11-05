"""/chatting's history: what webhooks and the sync have stored of each model's chats,
the webhook destinations feeding them, and accounts connected without an API key."""

from __future__ import annotations

import time
from functools import lru_cache
from typing import TYPE_CHECKING

from ofmhelpers.config import settings
from ofmhelpers.db.repositories import ChatRepository
from ofmhelpers.schemas.chatting import (
    ChatAccount,
    ChatDelivery,
    ChatPlatform,
    ChatRead,
    ChatTokens,
    ChatWebhook,
    DeliveryKind,
    FanMessage,
    ModelReach,
    OpenWait,
    StoredNotification,
)

if TYPE_CHECKING:
    from collections.abc import Callable


@lru_cache(maxsize=1)
def _repository() -> ChatRepository:
    return ChatRepository()


def last_read(platform: ChatPlatform) -> dict[str, str]:
    return _repository().last_read(platform)


def record_events(platform: ChatPlatform, read: ChatRead) -> None:
    _repository().record_events(platform, read)


def mark_read(platform: ChatPlatform, read: ChatRead) -> None:
    _repository().mark_read(platform, read)


def forget_open(platform: ChatPlatform, channel_id: str) -> None:
    _repository().forget_open(platform, channel_id)


def forget_message(platform: ChatPlatform, message_id: str) -> None:
    _repository().forget_message(platform, message_id)


def forget_model_messages(before: float) -> None:
    _repository().forget_model_messages(before)


def models(platform: ChatPlatform) -> list[str]:
    return _repository().models(platform)


def fan_messages(
    platform: ChatPlatform, username: str, since: float
) -> list[FanMessage]:
    rows = _repository().fan_messages(platform, username, since)
    return [FanMessage.model_validate(row) for row in rows]


def open_waits(before: float) -> list[OpenWait]:
    """Every platform's."""
    return [OpenWait.model_validate(row) for row in _repository().open_waits(before)]


def save_notification(
    platform: ChatPlatform, delivery: ChatDelivery, username: str
) -> None:
    _repository().save_notification(
        platform,
        delivery.event_id,
        username,
        delivery.kind,
        delivery.amount_cents,
        delivery.currency,
        delivery.detail,
        delivery.at or time.time(),
    )


def models_reaching_chats(
    platform: ChatPlatform, since: float, min_chats: int
) -> list[ModelReach]:
    return [
        ModelReach.model_validate(row)
        for row in _repository().models_reaching_chats(platform, since, min_chats)
    ]


def recent_mass_messages(
    platform: ChatPlatform, username: str, since: float
) -> list[str]:
    return _repository().recent_events(platform, username, DeliveryKind.MASS, since)


def notifications(since: float) -> list[StoredNotification]:
    """Every platform's."""
    return [
        StoredNotification.model_validate(row)
        for row in _repository().notifications(since)
    ]


def open_channels(platform: ChatPlatform) -> set[str]:
    return _repository().open_channels(platform)


def webhooks(platform: ChatPlatform) -> list[ChatWebhook]:
    return [ChatWebhook.model_validate(row) for row in _repository().webhooks(platform)]


def webhook_targets(platform: ChatPlatform) -> list[tuple[str, str]]:
    return _repository().webhook_targets(platform)


def save_webhook(platform: ChatPlatform, webhook: ChatWebhook) -> None:
    _repository().save_webhook(platform, webhook)


def accounts(platform: ChatPlatform) -> list[ChatAccount]:
    """Those connected on this server."""
    return [
        ChatAccount.model_validate(row)
        for row in _repository().accounts(platform, settings.web.base_url)
    ]


def save_account(
    platform: ChatPlatform, account: ChatAccount, tokens: ChatTokens
) -> None:
    _repository().save_account(platform, account, tokens, settings.web.base_url)


def fresh_token(
    platform: ChatPlatform,
    account_id: str,
    renew: Callable[[str], ChatTokens | None],
    *,
    valid_until: float,
) -> str | None:
    return _repository().fresh_token(
        platform, account_id, renew, valid_until, settings.web.base_url
    )
