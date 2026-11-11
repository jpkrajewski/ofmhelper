"""/chatting's history: what webhooks and the sync have stored of each model's chats,
the webhook destinations feeding them, and accounts connected without an API key."""

import time
from functools import lru_cache
lazy from collections.abc import Callable

from ofmhelpers.config import settings
from ofmhelpers.db.repositories import ChatRepository
from ofmhelpers.schemas.chatting import (
    ChatAccount,
    ChatDelivery,
    ChatPlatform,
    ChatPosition,
    ChatRead,
    ChatTokens,
    ChatWebhook,
    DeliveryKind,
    FanMessage,
    ModelReach,
    ModelTotal,
    OpenWait,
    StoredNotification,
    WebhookTarget,
)


@lru_cache(maxsize=1)
def _repository() -> ChatRepository:
    return ChatRepository()


def last_read(platform: ChatPlatform) -> list[ChatPosition]:
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
    return _repository().fan_messages(platform, username, since)


def open_waits(before: float) -> list[OpenWait]:
    """Every platform's."""
    return _repository().open_waits(before)


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


def save_sale(platform: ChatPlatform, delivery: ChatDelivery, username: str) -> None:
    """Every sale, small ones too (the daily report sums them)."""
    _repository().save_sale(
        platform,
        delivery.event_id,
        username,
        delivery.amount_cents or 0,
        delivery.currency or "USD",
        delivery.detail,
        delivery.at or time.time(),
    )


def sales_between(since: float, until: float) -> list[ModelTotal]:
    """Each model's sales in [since, until), every platform."""
    return _repository().sales_between(since, until)


def counts_between(kind: DeliveryKind, since: float, until: float) -> list[ModelTotal]:
    """Each model's new subscribers or mass messages in [since, until)."""
    return _repository().counts_between(kind, since, until)


def models_reaching_chats(
    platform: ChatPlatform, since: float, min_chats: int
) -> list[ModelReach]:
    return _repository().models_reaching_chats(platform, since, min_chats)


def recent_mass_messages(
    platform: ChatPlatform, username: str, since: float
) -> list[str]:
    return _repository().recent_events(platform, username, DeliveryKind.MASS, since)


def notifications(since: float) -> list[StoredNotification]:
    """Every platform's."""
    return _repository().notifications(since)


def open_channels(platform: ChatPlatform) -> set[str]:
    return _repository().open_channels(platform)


def webhooks(platform: ChatPlatform) -> list[ChatWebhook]:
    return _repository().webhooks(platform)


def webhook_targets(platform: ChatPlatform) -> list[WebhookTarget]:
    return _repository().webhook_targets(platform)


def save_webhook(platform: ChatPlatform, webhook: ChatWebhook) -> None:
    _repository().save_webhook(platform, webhook)


def accounts(platform: ChatPlatform) -> list[ChatAccount]:
    """Those connected on this server."""
    return _repository().accounts(platform, settings.web.base_url)


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
