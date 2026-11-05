"""Fanvue API shapes, the fields this app reads (official spec:
https://api.fanvue.com/docs/openapi-v1.json). Times are ISO 8601 on the wire."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 -- pydantic reads it at runtime

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
    # One by one in the client: a chat that cannot be read must not lose the page.
    data: list[dict]
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
    """Where the next, older page starts. Both null: no more."""

    sent_before: str | None = Field(default=None, alias="sentBefore")
    received_before: str | None = Field(default=None, alias="receivedBefore")


class MessagesPage(_Api):
    data: list[FanvueMessage]
    # Null on the last page (real replies, 2026-11-05: the spec shows an object).
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
    data: dict = Field(default_factory=dict)


class MessageEvent(_Api):
    """`creator.message.received`, `.sent` and `.deleted`. A deleted one has no
    `created_at`."""

    uuid: str
    created_at: datetime | None = None
    text: str | None = None
    creator: EventParty
    fan: EventParty


class ReactionEvent(_Api):
    """`creator.message.reaction`. `sender` says who reacted, `message_uuid` to what;
    the reaction has no id of its own."""

    sender: str
    message_uuid: str
    created_at: datetime | None = None
    creator: EventParty
    fan: EventParty


class SubscriptionEvent(_Api):
    """`creator.subscription.activated`: a new subscriber, or one coming back."""

    creator: EventParty


class PaymentEvent(_Api):
    """`creator.payment.succeeded`: a paid post, message, media, a tip, or a
    subscription charge, renewals included. `gross` is what the fan paid, in cents.
    `id` is the invoice number, the same if Fanvue repeats the event."""

    id: str
    source: str = ""
    billing_reason: str = ""
    gross: int
    currency: str | None = None
    paid_at: datetime | None = None
    creator: EventParty


class MassMessageEvent(_Api):
    """`creator.message.mass_sent`: once per send, and the recipients are not said."""

    uuid: str
    creator: EventParty
