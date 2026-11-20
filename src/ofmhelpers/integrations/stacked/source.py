from typing import Protocol
lazy from collections.abc import Iterator, Mapping

from ofmhelpers.integrations.stacked import history, webhooks
from ofmhelpers.integrations.stacked.client import StackedClient, reach
from ofmhelpers.integrations.stacked.errors import (
    StackedAuthError,
    StackedForbiddenError,
)
from ofmhelpers.log import get_logger
from ofmhelpers.schemas.chatting import (
    ChatDelivery,
    ChatModel,
    ChatModels,
    ChatPlatform,
    ChatRead,
    CreatedSubscription,
    ReadScope,
    Subscription,
)

logger = get_logger(__name__)


class KeyRing(Protocol):
    def keys(self) -> list[str]: ...


class StackedSource:
    platform = ChatPlatform.STACKED
    mass_reaches_chats = True
    bulk_sends_unflagged = True
    events = webhooks.EVENTS

    def __init__(self, keys: KeyRing) -> None:
        self._keys = keys

    def configured(self) -> bool:
        return bool(self._keys.keys())

    def models(self) -> ChatModels:
        reached = reach(self._keys.keys())
        return ChatModels(
            found=[
                ChatModel(
                    model_id=r.creator.user_id,
                    username=r.creator.username,
                    client=r.client,
                    owner=r.creator.user_id,
                )
                for r in reached.found
            ],
            failed=reached.failed,
        )

    def new_reads(self, model: ChatModel, scope: ReadScope) -> Iterator[ChatRead]:
        return history.new_reads(
            model.client,
            model.model_id,
            model.username,
            scope.seen,
            scope.oldest,
            scope.open_channels,
        )

    def shared_secret(self) -> str:
        return ""

    def subscriptions(self, model: ChatModel) -> list[Subscription]:
        return [
            Subscription(id=w.id, url=w.url, events=w.events)
            for w in model.client.webhooks(model.model_id)
        ]

    def subscribe(self, model: ChatModel, url: str) -> CreatedSubscription:
        self._retire_elsewhere(model, url)
        made = model.client.create_webhook(model.model_id, url, webhooks.EVENTS)
        return CreatedSubscription(id=made.id, url=made.url, secret=made.secret)

    def _retire_elsewhere(self, model: ChatModel, url: str) -> None:
        every_key = self._keys.keys()
        for api_key in every_key:
            other = StackedClient(api_key)
            if other.same_key(model.client):
                continue
            try:
                if model.model_id not in {c.user_id for c in other.info().creators}:
                    continue
                for hook in other.webhooks(model.model_id):
                    if hook.url == url:
                        other.delete_webhook(model.model_id, hook.id)
                        logger.info(
                            "stacked %s: destination of key %s retired",
                            model.username,
                            other.key_hint,
                        )
            except StackedAuthError, StackedForbiddenError:
                logger.warning(
                    "stacked %s: key %s refused, its destination left",
                    model.username,
                    other.key_hint,
                    exc_info=True,
                )

    def unsubscribe(self, model: ChatModel, subscription_id: str) -> None:
        model.client.delete_webhook(model.model_id, subscription_id)

    def answers_unsigned(self, raw: bytes, headers: Mapping[str, str]) -> bool:  # noqa: ARG002
        return False

    def verified(self, secret: str, raw: bytes, headers: Mapping[str, str]) -> bool:
        return webhooks.verified(secret, raw, headers)

    def deliveries(self, raw: bytes) -> list[ChatDelivery]:
        return webhooks.deliveries(raw)
