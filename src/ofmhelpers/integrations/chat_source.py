from typing import Protocol
lazy from collections.abc import Iterator, Mapping

lazy from ofmhelpers.db.chats.schema import (
    ChatDelivery,
    ChatModel,
    ChatModels,
    ChatPlatform,
    ChatRead,
    CreatedSubscription,
    ReadScope,
    Subscription,
)


class ChatSourceError(Exception): ...


class ChatSource(Protocol):
    platform: ChatPlatform
    mass_reaches_chats: bool
    bulk_sends_unflagged: bool
    events: list[str]

    def configured(self) -> bool: ...

    def models(self) -> ChatModels: ...

    def new_reads(self, model: ChatModel, scope: ReadScope) -> Iterator[ChatRead]: ...

    def shared_secret(self) -> str: ...

    def subscriptions(self, model: ChatModel) -> list[Subscription]: ...

    def subscribe(self, model: ChatModel, url: str) -> CreatedSubscription: ...

    def unsubscribe(self, model: ChatModel, subscription_id: str) -> None: ...

    def answers_unsigned(self, raw: bytes, headers: Mapping[str, str]) -> bool: ...

    def verified(self, secret: str, raw: bytes, headers: Mapping[str, str]) -> bool: ...

    def deliveries(self, raw: bytes) -> list[ChatDelivery]: ...
