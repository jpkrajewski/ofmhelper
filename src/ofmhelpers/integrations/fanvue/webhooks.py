"""Fanvue's webhooks: telling a real delivery from a forged one, and what one means
in the app's terms.

`X-Fanvue-Signature: t=<unix seconds>,v0=<hex>`: HMAC-SHA256 over `<t>.<raw body>`,
keyed with the whole secret string (`whsec_` included). While a secret rotates the
header carries a second bare MAC: `t=..,v0=<old>,<new>`. Before making a destination
Fanvue checks the address with an unsigned `fanvue.webhook.verification`, which must
get a 2xx, or the destination is not made."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import TYPE_CHECKING

from ofmhelpers.config import settings
from ofmhelpers.integrations.fanvue.models import (
    MassMessageEvent,
    MessageEvent,
    PaymentEvent,
    ReactionEvent,
    SubscriptionEvent,
    WebhookEnvelope,
)
from ofmhelpers.schemas.chatting import (
    ChatDelivery,
    ChatEvent,
    ChatRead,
    DeliveryKind,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

_PROBE = "fanvue.webhook.verification"
_SIGNATURE = "x-fanvue-signature"
MESSAGE_RECEIVED = "creator.message.received"
MESSAGE_SENT = "creator.message.sent"
MESSAGE_DELETED = "creator.message.deleted"
MASS_SENT = "creator.message.mass_sent"
REACTION = "creator.message.reaction"
SUBSCRIBED = "creator.subscription.activated"
PAYMENT = "creator.payment.succeeded"
EVENTS = [
    MESSAGE_RECEIVED,
    MESSAGE_SENT,
    MESSAGE_DELETED,
    MASS_SENT,
    REACTION,
    SUBSCRIBED,
    PAYMENT,
]
_RENEWAL = "subscription_renewal"
_FROM_CREATOR = "creator"


def channel_of(creator_uuid: str, fan_uuid: str) -> str:
    """Fanvue has no chat id: a chat is a creator and a fan, and one fan can write
    to two creators."""
    return f"{creator_uuid}:{fan_uuid}"


def is_probe(raw: bytes, headers: Mapping[str, str]) -> bool:
    if _SIGNATURE in headers:
        return False
    try:
        return json.loads(raw).get("type") == _PROBE
    except (ValueError, AttributeError):
        return False


def _signature_parts(header: str) -> tuple[str, list[str]]:
    """(t, MACs). A bare field belongs to the key before it."""
    timestamp = ""
    macs: list[str] = []
    key = ""
    for field in header.split(","):
        name, separator, value = field.partition("=")
        if separator:
            key = name.strip()
        else:
            value = name
        if key == "t":
            timestamp = value.strip()
        elif key == "v0":
            macs.append(value.strip())
    return timestamp, macs


def _fresh(timestamp: str) -> bool:
    try:
        age_s = abs(time.time() - int(timestamp))
    except (ValueError, OverflowError):
        return False
    return age_s <= settings.chatting.webhook_tolerance_s


def verified(secret: str, raw: bytes, headers: Mapping[str, str]) -> bool:
    timestamp, macs = _signature_parts(headers.get(_SIGNATURE, ""))
    if not macs or not _fresh(timestamp):
        return False
    signed = f"{timestamp}.".encode() + raw
    expected = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest().encode()
    return any(hmac.compare_digest(mac.encode(), expected) for mac in macs)


def _message_read(event: MessageEvent, *, from_fan: bool) -> ChatRead:
    text = (event.text or "[attachment]").replace("\x00", "")
    at = event.created_at.timestamp() if event.created_at else time.time()
    return ChatRead(
        channel_id=channel_of(event.creator.uuid, event.fan.uuid),
        model_username="",
        fan_name=event.fan.display_name or event.fan.handle or event.fan.uuid,
        last_message_id=event.uuid,
        events=[ChatEvent(message_id=event.uuid, from_fan=from_fan, at=at, text=text)],
    )


def _reaction_replies(event: ReactionEvent) -> list[ChatDelivery]:
    """The model's emoji on a fan's message answers it; a fan's reaction says nothing."""
    if event.sender != _FROM_CREATOR:
        return []
    at = event.created_at.timestamp() if event.created_at else time.time()
    reply_id = f"{event.message_uuid}:reaction"
    read = ChatRead(
        channel_id=channel_of(event.creator.uuid, event.fan.uuid),
        model_username="",
        fan_name=event.fan.display_name or event.fan.handle or event.fan.uuid,
        last_message_id=reply_id,
        events=[ChatEvent(message_id=reply_id, from_fan=False, at=at, text="")],
    )
    return [
        ChatDelivery(kind=DeliveryKind.MESSAGE, model_id=event.creator.uuid, read=read)
    ]


def deliveries(raw: bytes) -> list[ChatDelivery]:
    envelope = WebhookEnvelope.model_validate_json(raw)
    if envelope.type == REACTION:
        return _reaction_replies(ReactionEvent.model_validate(envelope.data))
    if envelope.type in (MESSAGE_RECEIVED, MESSAGE_SENT):
        event = MessageEvent.model_validate(envelope.data)
        read = _message_read(event, from_fan=envelope.type == MESSAGE_RECEIVED)
        return [
            ChatDelivery(
                kind=DeliveryKind.MESSAGE, model_id=event.creator.uuid, read=read
            )
        ]
    if envelope.type == MESSAGE_DELETED:
        event = MessageEvent.model_validate(envelope.data)
        return [
            ChatDelivery(
                kind=DeliveryKind.DELETED,
                model_id=event.creator.uuid,
                message_id=event.uuid,
            )
        ]
    if envelope.type == MASS_SENT:
        mass = MassMessageEvent.model_validate(envelope.data)
        return [
            ChatDelivery(
                kind=DeliveryKind.MASS, model_id=mass.creator.uuid, event_id=mass.uuid
            )
        ]
    if envelope.type == SUBSCRIBED:
        subscription = SubscriptionEvent.model_validate(envelope.data)
        return [
            ChatDelivery(
                kind=DeliveryKind.SUBSCRIBED,
                model_id=subscription.creator.uuid,
                event_id=envelope.id,
            )
        ]
    if envelope.type == PAYMENT:
        payment = PaymentEvent.model_validate(envelope.data)
        renewal = payment.billing_reason == _RENEWAL
        return [
            ChatDelivery(
                kind=DeliveryKind.SALE,
                model_id=payment.creator.uuid,
                event_id=payment.id,
                amount_cents=payment.gross,
                currency=payment.currency,
                detail="renewal" if renewal else payment.source,
                at=payment.paid_at.timestamp() if payment.paid_at else None,
            )
        ]
    return []
