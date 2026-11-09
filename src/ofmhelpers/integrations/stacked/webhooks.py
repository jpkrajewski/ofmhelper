"""Stacked's webhooks: telling a real delivery from a forged one, and what one
means in the app's terms.

Deliveries are signed by Svix: base64 of HMAC-SHA256 over `{id}.{timestamp}.{raw
body}`, keyed with the base64 after `whsec_` in the secret; `svix-signature` lists
`v1,<signature>` entries, more than one while a secret rotates. The stdlib does
all of it, so no Svix package."""

import base64
import binascii
import hashlib
import hmac
import time
lazy from collections.abc import Mapping

from ofmhelpers.config import settings
from ofmhelpers.integrations.stacked.history import chat_event
from ofmhelpers.integrations.stacked.models import (
    MassMessageEventData,
    MessageEventData,
    WebhookEnvelope,
)
from ofmhelpers.schemas.chatting import ChatDelivery, ChatRead, DeliveryKind

MESSAGE_CREATED = "message.created"
MESSAGE_DELETED = "message.deleted"
MASS_MESSAGE_COMPLETED = "mass_message.completed"
EVENTS = [MESSAGE_CREATED, MESSAGE_DELETED, MASS_MESSAGE_COMPLETED]


def _fresh(timestamp: str) -> bool:
    """Not a recording of an old delivery played back. Anything that is not a
    sane number is simply not fresh."""
    try:
        age_s = abs(time.time() - int(timestamp))
    except ValueError, OverflowError:
        return False
    return age_s <= settings.chatting.webhook_tolerance_s


def verified(secret: str, raw: bytes, headers: Mapping[str, str]) -> bool:
    """Whether `raw` is what Stacked signed with `secret`, just now."""
    message_id = headers.get("svix-id", "")
    timestamp = headers.get("svix-timestamp", "")
    if not message_id or not _fresh(timestamp):
        return False
    try:
        key = base64.b64decode(secret.removeprefix("whsec_"))
    except binascii.Error:
        return False
    signed = f"{message_id}.{timestamp}.".encode() + raw
    expected = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest())
    # As bytes: compare_digest refuses a str that is not plain ASCII.
    return any(
        hmac.compare_digest(entry.removeprefix("v1,").encode(), expected)
        for entry in headers.get("svix-signature", "").split()
    )


def _message_read(data: MessageEventData) -> ChatRead:
    """A `message.created` as one chat's read: the sender is the fan unless it is
    the model. It does not say how far the sync has read."""
    event = chat_event(data.message, data.creator_user_id)
    nickname = data.sender.nickname if data.sender else None
    return ChatRead(
        channel_id=data.channel.channel_url,
        model_username="",
        fan_name=nickname or data.message.user_id,
        last_message_id=str(data.message.message_id),
        events=[event] if event else [],
    )


def deliveries(raw: bytes) -> list[ChatDelivery]:
    envelope = WebhookEnvelope.model_validate_json(raw)
    if envelope.type == MESSAGE_CREATED:
        data = MessageEventData.model_validate(envelope.data)
        read = _message_read(data)
        return [
            ChatDelivery(
                kind=DeliveryKind.MESSAGE, model_id=data.creator_user_id, read=read
            )
        ]
    if envelope.type == MESSAGE_DELETED:
        data = MessageEventData.model_validate(envelope.data)
        return [
            ChatDelivery(
                kind=DeliveryKind.DELETED,
                model_id=data.creator_user_id,
                message_id=str(data.message.message_id),
            )
        ]
    if envelope.type == MASS_MESSAGE_COMPLETED:
        mass = MassMessageEventData.model_validate(envelope.data)
        return [
            ChatDelivery(
                kind=DeliveryKind.MASS,
                model_id=mass.creator_user_id,
                event_id=mass.mass_message_id,
            )
        ]
    return []
