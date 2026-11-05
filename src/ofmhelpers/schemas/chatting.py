"""The chat platforms /chatting lists, what it stores of a chat and what its
pages show, in the same terms for every platform. A new platform = one
`ChatPlatform` member and one entry in `features/chatting/sources.py` (its page says
"Not connected yet" until something fills the store)."""

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class ChatPlatform(StrEnum):
    ONLYFANS = "onlyfans"
    FANVUE = "fanvue"
    STACKED = "stacked"

    @property
    def label(self) -> str:
        return {"onlyfans": "OnlyFans", "fanvue": "Fanvue", "stacked": "Stacked"}[self]

    def model_page(self, username: str) -> str:
        """Where a pop-up naming this model links: who is waiting, not their
        public profile."""
        return f"/chatting/{self.value}/{username}"


class RecentModel(BaseModel):
    platform: ChatPlatform
    username: str


class ChatModel(BaseModel):
    """A model a platform's credentials reach. `client` is the platform's own, for
    its own use; nothing else looks inside."""

    model_id: str
    username: str
    client: Any = Field(default=None, exclude=True, repr=False)
    # Who the platform ties a webhook destination to: one model, or an agency
    # covering all of its models with one.
    owner: str = ""


class ChatModels(BaseModel):
    """`failed`: the platform did not answer for some credentials, so someone may
    be missing."""

    found: list[ChatModel]
    failed: bool = False


class ReadScope(BaseModel):
    """What one sync round needs to know. `seen`: {channel: last message read}.
    `oldest`: how far back a chat's first read goes. `open_channels`: chats with a
    fan waiting. `thorough`: read everything, not just what changed lately."""

    seen: dict[str, str]
    oldest: datetime
    open_channels: set[str]
    thorough: bool


class ChatEvent(BaseModel):
    """One message of a chat: the fan's, or the model's (a mass message is the
    model's like any other, so it counts as a reply)."""

    message_id: str
    from_fan: bool
    at: float
    text: str = ""


class ChatRead(BaseModel):
    """What one read of a chat found: its new messages, oldest first.
    `cannot_answer`: the fan blocked or left, so nothing there can wait."""

    channel_id: str
    model_username: str
    fan_name: str
    last_message_id: str
    events: list[ChatEvent]
    cannot_answer: bool = False

    @field_validator("fan_name")
    @classmethod
    def _storable_name(cls, value: str) -> str:
        """Whatever a fan calls themselves: Postgres refuses a NUL, the column is 255."""
        return value.replace("\x00", "")[:255]


class DeliveryKind(StrEnum):
    MESSAGE = "message"
    DELETED = "deleted"
    MASS = "mass"
    SUBSCRIBED = "subscribed"
    SALE = "sale"


class ChatDelivery(BaseModel):
    """A webhook delivery in the app's terms, whatever the platform sent. `read`
    for a message; `message_id` for a deleted message; the rest for a mass message, a
    new subscriber or a sale. `event_id` stays the same when the platform repeats an
    event. `amount_cents` is what the fan paid, before fees."""

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
    # Shown only when it is made, never again.
    secret: str = Field(repr=False)


class FanMessage(BaseModel):
    channel_id: str
    fan_name: str
    text: str
    sent_at: float
    replied_at: float | None = None


class ModelReach(BaseModel):
    """A model writing in many chats at once, since `started_at`."""

    model_username: str
    started_at: float


class OpenWait(BaseModel):
    """A chat whose fan is waiting, since their first unanswered message."""

    platform: ChatPlatform
    model_username: str
    channel_id: str
    started_at: float

    @property
    def id(self) -> str:
        """Names this wait: a fan answered who writes again is a new one."""
        return f"{self.platform.value}:{self.channel_id}:{self.started_at}"


class LateWait(BaseModel):
    """A fan left waiting too long, as the page prints it: the message that
    started the wait, and when it ended."""

    sent: str
    replied: str
    waited: str
    fan_name: str
    text: str


class ModelReport(BaseModel):
    username: str
    # The average of the answered waits; None when none was answered.
    average_response_time: str | None
    period_h: int
    late_after_min: int
    late: list[LateWait]


class ChatWebhook(BaseModel):
    """A destination made on a platform for one model, with its signing secret."""

    model_id: str
    model_username: str
    webhook_id: str
    url: str
    secret: str = Field(repr=False)


class ChatAccount(BaseModel):
    """Someone who connected a platform that has no API keys. No tokens here: only
    the token keeper handles those."""

    account_id: str
    username: str
    is_agency: bool = False
    needs_reconnect: bool = False


class ChatTokens(BaseModel):
    access_token: str = Field(repr=False)
    refresh_token: str = Field(repr=False)
    expires_at: float


class StoredNotification(BaseModel):
    platform: ChatPlatform
    event_id: str
    model_username: str
    kind: DeliveryKind
    amount_cents: int | None = None
    currency: str | None = None
    detail: str = ""


class ChatPopup(BaseModel):
    """A pop-up: `{text}: @{username}`. `event_ids`: what it stands for, one event or
    every fan waiting on a model; closed, it stays away until one of them is new.
    `kind` picks its colours."""

    id: str
    event_ids: list[str]
    kind: Literal["info", "warn"] = "info"
    text: str
    username: str
    url: str


class ChatAlerts(BaseModel):
    """What every admin page's chat pop-ups name, and when to ask again."""

    popups: list[ChatPopup]
    check_every_s: float
