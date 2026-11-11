"""Stacked as a chat source (`integrations/chat_source.py`): API keys in `.env`."""

lazy from collections.abc import Iterator, Mapping

from ofmhelpers.config import settings
from ofmhelpers.integrations.stacked import history, webhooks
from ofmhelpers.integrations.stacked.client import reach
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


class StackedSource:
    platform = ChatPlatform.STACKED
    # Expected from the spec's `message.created`; not yet seen on a real mass send.
    mass_reaches_chats = True
    # Seen 2026-11-05: 650 chats in 10 minutes, no flag and no `mass_message.completed`.
    bulk_sends_unflagged = True
    events = webhooks.EVENTS

    def configured(self) -> bool:
        return bool(settings.stacked.api_keys)

    def models(self) -> ChatModels:
        reached = reach(k.key for k in settings.stacked.api_keys)
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
        made = model.client.create_webhook(model.model_id, url, webhooks.EVENTS)
        return CreatedSubscription(id=made.id, url=made.url, secret=made.secret)

    def unsubscribe(self, model: ChatModel, subscription_id: str) -> None:
        model.client.delete_webhook(model.model_id, subscription_id)

    def answers_unsigned(self, raw: bytes, headers: Mapping[str, str]) -> bool:  # noqa: ARG002
        return False

    def verified(self, secret: str, raw: bytes, headers: Mapping[str, str]) -> bool:
        return webhooks.verified(secret, raw, headers)

    def deliveries(self, raw: bytes) -> list[ChatDelivery]:
        return webhooks.deliveries(raw)
