"""Stacked as a chat source (`integrations/chat_source.py`): its API keys come
from a `KeyRing` (the app's: `.env` lines and keys saved on /account,
`features/chatting/stacked_keys.py`)."""

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
    # Expected from the spec's `message.created`; not yet seen on a real mass send.
    mass_reaches_chats = True
    # Seen 2026-11-05: 650 chats in 10 minutes, no flag and no `mass_message.completed`.
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
        """A destination is one key's: when a creator moves to another key (an
        agency key added that reaches it), the one its old key made at `url`
        goes, before the new one is made. Left, it would keep delivering here
        with a secret no longer held, and 30 failed signatures from one address
        turn that address away. Only at `url`, this server's own address; asked
        only while a destination is being made, never in a normal round. Stacked
        failing on the way raises: no new destination this round, the move is
        tried again next round (Kimi review 2026-11-17: made anyway, the old one
        was never asked again). A key Stacked refuses can delete nothing: skipped."""
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
