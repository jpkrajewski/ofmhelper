# Copyright (C) 2026 jpkrajewski

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class _Api(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")


class FanvueUser(_Api):
    uuid: str
    handle: str
    roles: list[str] = Field(default_factory=list)


class ChatUser(_Api):
    uuid: str
    handle: str = ""
    display_name: str | None = Field(default=None, alias="displayName")
    nickname: str | None = None


class LastMessage(_Api):
    uuid: str
    sent_at: datetime | None = Field(default=None, alias="sentAt")
    type: str = ""


class FanvueChat(_Api):
    user: ChatUser
    last_message: LastMessage | None = Field(default=None, alias="lastMessage")


class ChatsPage(_Api):
    data: list[dict[str, Any]]
    next_cursor: str | None = Field(default=None, alias="nextCursor")


class CreatorsPage(_Api):
    data: list[ChatUser]
    next_cursor: str | None = Field(default=None, alias="nextCursor")


class MessageSender(_Api):
    uuid: str


class FanvueMessage(_Api):
    uuid: str
    text: str | None = None
    sent_at: datetime | None = Field(default=None, alias="sentAt")
    sender: MessageSender
    type: str = ""
    status: str = ""


class DateFilter(_Api):
    sent_before: str | None = Field(default=None, alias="sentBefore")
    received_before: str | None = Field(default=None, alias="receivedBefore")


class MessagesPage(_Api):
    data: list[FanvueMessage]
    date_filter: DateFilter | None = Field(default=None, alias="dateFilter")


class SubscriptionItem(_Api):
    id: str
    url: str
    events: list[str] = Field(default_factory=list)


class SubscriptionsPage(_Api):
    data: list[SubscriptionItem]


class CreatedSubscriptionReply(_Api):
    id: str
    signing_secret: str = Field(alias="signingSecret", repr=False)


class TokenReply(_Api):
    access_token: str = Field(repr=False)
    refresh_token: str = Field(repr=False)
    expires_in: float


class EventParty(_Api):
    uuid: str
    handle: str | None = None
    display_name: str | None = None


class WebhookEnvelope(_Api):
    id: str
    type: str
    data: dict[str, Any] = Field(default_factory=dict)


class MessageEvent(_Api):
    uuid: str
    created_at: datetime | None = None
    text: str | None = None
    creator: EventParty
    fan: EventParty


class ReactionEvent(_Api):
    sender: str
    message_uuid: str
    created_at: datetime | None = None
    creator: EventParty
    fan: EventParty


class SubscriptionEvent(_Api):
    creator: EventParty


class PaymentEvent(_Api):
    id: str
    source: str = ""
    billing_reason: str = ""
    gross: int
    currency: str | None = None
    paid_at: datetime | None = None
    creator: EventParty


class MassMessageEvent(_Api):
    uuid: str
    creator: EventParty
