# Copyright (C) 2026 jpkrajewski

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class ChatPlatform(StrEnum):
    ONLYFANS = "onlyfans"
    FANVUE = "fanvue"
    STACKED = "stacked"

    @property
    def label(self) -> str:
        return {"onlyfans": "OnlyFans", "fanvue": "Fanvue", "stacked": "Stacked"}[self]

    def model_page(self, username: str) -> str:
        return f"/chatting/{self.value}/{username}"


class RecentModel(BaseModel):
    platform: ChatPlatform
    username: str


class ChatModel(BaseModel):
    model_id: str
    username: str
    client: Any = Field(default=None, exclude=True, repr=False)
    owner: str = ""


class ChatModels(BaseModel):
    found: list[ChatModel]
    failed: bool = False
    unread: bool = False


class ChatPosition(BaseModel):
    model_config = ConfigDict(frozen=True)

    channel_id: str
    last_message_id: str


class ReadScope(BaseModel):
    seen: dict[str, str]
    oldest: datetime
    open_channels: set[str]
    thorough: bool

    @classmethod
    def since(
        cls,
        read: list[ChatPosition],
        oldest: datetime,
        open_channels: set[str],
        *,
        thorough: bool,
    ) -> ReadScope:
        return cls(
            seen={p.channel_id: p.last_message_id for p in read},
            oldest=oldest,
            open_channels=open_channels,
            thorough=thorough,
        )


class ChatEvent(BaseModel):
    message_id: str
    from_fan: bool
    at: float
    text: str = ""


class ChatRead(BaseModel):
    channel_id: str
    model_username: str
    fan_name: str
    last_message_id: str
    events: list[ChatEvent]
    cannot_answer: bool = False

    @field_validator("fan_name")
    @classmethod
    def _storable_name(cls, value: str) -> str:
        return value.replace("\x00", "")[:255]


class DeliveryKind(StrEnum):
    MESSAGE = "message"
    DELETED = "deleted"
    MASS = "mass"
    SUBSCRIBED = "subscribed"
    SALE = "sale"


class ChatDelivery(BaseModel):
    kind: DeliveryKind
    model_id: str
    read: ChatRead | None = None
    message_id: str = ""
    event_id: str = ""
    amount_cents: int | None = None
    currency: str | None = None
    detail: str = ""
    at: float | None = None


class Subscription(BaseModel):
    id: str
    url: str
    events: list[str] = Field(default_factory=list)


class CreatedSubscription(Subscription):
    secret: SecretStr = Field(repr=False)


class FanMessage(BaseModel):
    channel_id: str
    fan_name: str
    text: str
    sent_at: float
    replied_at: float | None = None


class ModelReach(BaseModel):
    model_username: str
    started_at: float


class ModelTotal(BaseModel):
    platform: ChatPlatform
    model_username: str
    count: int
    cents: int = 0


class OpenWait(BaseModel):
    platform: ChatPlatform
    model_username: str
    channel_id: str
    started_at: float
    fan_name: str = ""

    @property
    def id(self) -> str:
        return f"{self.platform.value}:{self.channel_id}:{self.started_at}"


class LateWait(BaseModel):
    sent: str
    replied: str
    waited: str
    fan_name: str
    text: str


class ModelReport(BaseModel):
    username: str
    average_response_time: str | None
    period_h: int
    late_after_min: int
    late: list[LateWait]


class ChatWebhook(BaseModel):
    model_id: str
    model_username: str
    webhook_id: str
    url: str
    secret: SecretStr = Field(repr=False)


class WebhookTarget(BaseModel):
    model_config = ConfigDict(frozen=True)

    model_username: str
    url: str


class ChatAccount(BaseModel):
    account_id: str
    username: str
    is_agency: bool = False
    needs_reconnect: bool = False


class ChatTokens(BaseModel):
    access_token: SecretStr = Field(repr=False)
    refresh_token: SecretStr = Field(repr=False)
    expires_at: float


class StoredNotification(BaseModel):
    platform: ChatPlatform
    event_id: str
    model_username: str
    kind: DeliveryKind
    amount_cents: int | None = None
    currency: str | None = None
    detail: str = ""
    at: float
