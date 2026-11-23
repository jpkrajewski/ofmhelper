# Copyright (C) 2026 jpkrajewski

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class _Api(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")


class AccountType(StrEnum):
    CREATOR = "creator"
    AGENCY = "agency"


class Account(_Api):
    id: str
    type: AccountType


class Me(_Api):
    account: Account
    name: str = ""
    scopes: list[str] = Field(default_factory=list)


class AccessibleCreator(_Api):
    user_id: str = Field(alias="userId")
    username: str
    display_name: str | None = Field(default=None, alias="displayName")


class CreatorsPage(_Api):
    creators: list[AccessibleCreator]


class MessageReaction(_Api):
    key: str
    user_ids: list[str] = Field(default_factory=list, alias="userIds")
    updated_at: int | None = Field(default=None, alias="updatedAt")


class Message(_Api):
    message_id: int = Field(alias="messageId")
    user_id: str = Field(default="", alias="userId")
    created_at: datetime = Field(alias="createdAt")
    type: str = ""
    custom_type: str = Field(default="", alias="customType")
    text: str = ""
    is_removed: bool = Field(default=False, alias="isRemoved")
    reactions: list[MessageReaction] = Field(default_factory=list)

    @field_validator("user_id", "type", "custom_type", "text", mode="before")
    @classmethod
    def _null_is_empty(cls, value: Any) -> Any:
        return "" if value is None else value

    @field_validator("reactions", mode="before")
    @classmethod
    def _null_is_none_yet(cls, value: Any) -> Any:
        return [] if value is None else value


class ChatMember(_Api):
    user_id: str = Field(alias="userId")
    nickname: str = ""
    display_name: str | None = Field(default=None, alias="displayName")
    is_blocked_by_creator: bool = Field(default=False, alias="isBlockedByCreator")
    is_blocking_creator: bool = Field(default=False, alias="isBlockingCreator")


class ChatSummary(_Api):
    channel_url: str = Field(alias="channelUrl")
    name: str = ""
    custom_type: str = Field(default="", alias="customType")
    member_user_ids: list[str] = Field(default_factory=list, alias="memberUserIds")
    members: list[ChatMember] = Field(default_factory=list)
    message_count: int = Field(default=0, alias="messageCount")
    unread_message_count: int = Field(default=0, alias="unreadMessageCount")
    last_message: Message | None = Field(default=None, alias="lastMessage")

    def answerable_fan(self, creator_user_id: str) -> ChatMember | None:
        fan = next((m for m in self.members if m.user_id != creator_user_id), None)
        if fan is None or fan.is_blocked_by_creator or fan.is_blocking_creator:
            return None
        return fan


class ChatsPage(_Api):
    chats: list[dict[str, Any]]
    next_cursor: str | None = Field(default=None, alias="nextCursor")


class MessagesPage(_Api):
    messages: list[Message]


class ErrorReply(_Api):
    error: str


class WebhookDestination(_Api):
    id: str
    url: str
    events: list[str] = Field(default_factory=list)


class WebhooksPage(_Api):
    webhooks: list[WebhookDestination]


class CreatedWebhook(WebhookDestination):
    secret: str


class WebhookEnvelope(_Api):
    id: str
    type: str
    data: dict[str, Any]


class WebhookChannel(_Api):
    channel_url: str = Field(alias="channelUrl")


class WebhookSender(_Api):
    nickname: str | None = None


class MessageEventData(_Api):
    creator_user_id: str = Field(alias="creatorUserId")
    channel: WebhookChannel
    message: Message
    sender: WebhookSender | None = None


class MassMessageEventData(_Api):
    creator_user_id: str = Field(alias="creatorUserId")
    mass_message_id: str = Field(alias="massMessageId")


class Earnings(_Api):
    currency: str
    gross_cents: int = Field(alias="grossUnitAmount")
    net_cents: int = Field(alias="netUnitAmount")


class EarningsPage(_Api):
    earnings: list[Earnings]


class SubscriberSummary(_Api):
    new: int = Field(alias="newSubscribers")
    returning: int = Field(alias="resubscribers")
    canceled: int = Field(alias="canceledSubscribers")
    active: int = Field(alias="activeSubscribers")


class SubscriberSummaryPage(_Api):
    summaries: list[SubscriberSummary]
