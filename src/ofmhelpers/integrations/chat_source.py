"""What a chat platform must offer for /chatting: its models, their new messages,
webhook destinations, and its deliveries in the app's own terms. Everything above
this (the webhook route, the sync, the pop-ups, the stats) knows nothing about any
platform, so a new platform is one class doing this and one entry in
`features/chatting/sources.py`."""

from typing import Protocol
lazy from collections.abc import Iterator, Mapping

from ofmhelpers.errors import AppError
lazy from ofmhelpers.schemas.chatting import (
    ChatDelivery,
    ChatModel,
    ChatModels,
    ChatPlatform,
    ChatRead,
    CreatedSubscription,
    ReadScope,
    Subscription,
)


class ChatSourceError(AppError):
    """A platform refused or failed. Each platform's own errors derive from this,
    so what runs every platform has one failure to handle."""


class ChatSource(Protocol):
    platform: ChatPlatform
    # Whether a mass message also delivers a message event for each chat it
    # reaches. If not, the sync has to read which waits it answered.
    mass_reaches_chats: bool
    # Whether a bulk send can go out with no event saying so. The model reaching many
    # chats at once is then the only sign of one.
    bulk_sends_unflagged: bool
    # What a webhook destination must deliver. One missing gets it remade.
    events: list[str]

    def configured(self) -> bool:
        """Whether this platform is set up at all. From settings only: asked on
        the event loop, so no database and no network."""
        ...

    def models(self) -> ChatModels:
        """The models this platform's credentials reach."""
        ...

    def new_reads(self, model: ChatModel, scope: ReadScope) -> Iterator[ChatRead]:
        """Each of the model's chats with something new, as it is read."""
        ...

    def shared_secret(self) -> str:
        """The signing secret of a destination set up once on the platform's own
        site, covering every model; empty when each destination is made here and
        has its own. From settings only."""
        ...

    def subscriptions(self, model: ChatModel) -> list[Subscription]: ...

    def subscribe(self, model: ChatModel, url: str) -> CreatedSubscription: ...

    def unsubscribe(self, model: ChatModel, subscription_id: str) -> None: ...

    def answers_unsigned(self, raw: bytes, headers: Mapping[str, str]) -> bool:
        """Whether this is the platform's own check of the address, which carries
        no signature, is answered and does nothing."""
        ...

    def verified(self, secret: str, raw: bytes, headers: Mapping[str, str]) -> bool:
        """Whether `raw` is what the platform signed with `secret`, just now."""
        ...

    def deliveries(self, raw: bytes) -> list[ChatDelivery]:
        """A signed delivery in the app's terms; empty for one /chatting has no
        use for. Raises pydantic's `ValidationError` when it cannot be understood."""
        ...
