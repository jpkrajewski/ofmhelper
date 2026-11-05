"""Stacked API shapes, the fields this app reads (`openapi.json` has them all).

Chat message times are epoch milliseconds and chat times epoch seconds on the
wire; pydantic tells the two apart, so every `created_at` here is a UTC
datetime."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 -- pydantic reads it at runtime
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
    """The account a key acts as: `creator` reaches that one creator, `agency`
    every creator with an accepted link to it."""

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
    # Milliseconds, like the message's own times.
    updated_at: int | None = Field(default=None, alias="updatedAt")


class Message(_Api):
    message_id: int = Field(alias="messageId")
    # Sender; empty on the provider's own events (`type` ADMM).
    user_id: str = Field(default="", alias="userId")
    created_at: datetime = Field(alias="createdAt")
    # MESG, FILE, ADMM; empty on a webhook's message (seen 2026-11-05).
    type: str = ""
    # "" for plain text; PPV_IMAGE, PAYMENT_REQUEST, VIDEO, IMAGE, ...
    custom_type: str = Field(default="", alias="customType")
    text: str = ""
    is_removed: bool = Field(default=False, alias="isRemoved")
    # Only on a chat's messages: the chat list's last message has no such field.
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
    # None on a chat nobody wrote in yet.
    last_message: Message | None = Field(default=None, alias="lastMessage")

    def answerable_fan(self, creator_user_id: str) -> ChatMember | None:
        """The chat's fan; None when they cannot be answered: they left, or one
        of the two blocked the other."""
        fan = next((m for m in self.members if m.user_id != creator_user_id), None)
        if fan is None or fan.is_blocked_by_creator or fan.is_blocking_creator:
            return None
        return fan


class ChatsPage(_Api):
    # One by one in the client: a chat that cannot be read must not lose the page.
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
    # Shown only here, never again.
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
    # The spec lets it be left out.
    sender: WebhookSender | None = None


class MassMessageEventData(_Api):
    creator_user_id: str = Field(alias="creatorUserId")
    mass_message_id: str = Field(alias="massMessageId")
