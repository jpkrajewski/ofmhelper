# Copyright (C) 2026 jpkrajewski

import logging
from functools import partial
from typing import NamedTuple, Protocol
lazy from collections.abc import Iterator, Mapping
lazy from datetime import datetime

from ofmhelpers.config import settings
from ofmhelpers.db.chats.schema import (
    ChatAccount,
    ChatDelivery,
    ChatEvent,
    ChatModel,
    ChatModels,
    ChatPlatform,
    ChatRead,
    CreatedSubscription,
    ReadScope,
    Subscription,
)
from ofmhelpers.integrations.fanvue import webhooks
from ofmhelpers.integrations.fanvue.client import FanvueClient
from ofmhelpers.integrations.fanvue.errors import FanvueError, FanvueRefusedError
lazy from ofmhelpers.integrations.fanvue.models import FanvueChat, FanvueMessage

logger = logging.getLogger(__name__)

_MASS_TYPES = frozenset({"BROADCAST", "GHOST_PROMOTION"})
_SENT = "SENT"
_NOT_A_CHAT_MESSAGE = frozenset({"MARKETING_KYC"})


class TokenKeeper(Protocol):
    def accounts(self) -> list[ChatAccount]: ...

    def access_token(self, account_id: str) -> str: ...


class _Handle(NamedTuple):
    client: FanvueClient
    creator_uuid: str | None


class FanvueSource:
    platform = ChatPlatform.FANVUE
    mass_reaches_chats = False
    bulk_sends_unflagged = False
    events = webhooks.EVENTS

    def __init__(self, tokens: TokenKeeper) -> None:
        self._tokens = tokens

    def configured(self) -> bool:
        return bool(settings.fanvue.client_id and settings.fanvue.client_secret)

    def models(self) -> ChatModels:
        found: dict[str, ChatModel] = {}
        failed = False
        unread = False
        for account in self._tokens.accounts():
            if account.needs_reconnect:
                unread = True
                continue
            client = FanvueClient(
                partial(self._tokens.access_token, account.account_id),
            )
            if not account.is_agency:
                found[account.account_id] = ChatModel(
                    model_id=account.account_id,
                    username=account.username,
                    client=_Handle(client, None),
                    owner=account.account_id,
                )
                continue
            try:
                for creator in client.managed_creators():
                    found.setdefault(
                        creator.uuid,
                        ChatModel(
                            model_id=creator.uuid,
                            username=creator.handle,
                            client=_Handle(client, creator.uuid),
                            owner=account.account_id,
                        ),
                    )
            except FanvueRefusedError:
                unread = True
                logger.warning(
                    "fanvue agency %s refused",
                    account.username,
                    exc_info=True,
                )
            except FanvueError:
                logger.warning(
                    "fanvue agency %s not read",
                    account.username,
                    exc_info=True,
                )
                failed = True
        return ChatModels(found=list(found.values()), failed=failed, unread=unread)

    def new_reads(self, model: ChatModel, scope: ReadScope) -> Iterator[ChatRead]:
        return _new_reads(model, scope)

    def shared_secret(self) -> str:
        return settings.fanvue.event_secret

    def subscriptions(self, model: ChatModel) -> list[Subscription]:
        return [
            Subscription(id=item.id, url=item.url, events=item.events)
            for item in model.client.client.subscriptions()
        ]

    def subscribe(self, model: ChatModel, url: str) -> CreatedSubscription:
        made = model.client.client.subscribe(url, webhooks.EVENTS)
        return CreatedSubscription(id=made.id, url=url, secret=made.signing_secret)

    def unsubscribe(self, model: ChatModel, subscription_id: str) -> None:
        model.client.client.unsubscribe(subscription_id)

    def answers_unsigned(self, raw: bytes, headers: Mapping[str, str]) -> bool:
        return webhooks.is_probe(raw, headers)

    def verified(self, secret: str, raw: bytes, headers: Mapping[str, str]) -> bool:
        return webhooks.verified(secret, raw, headers)

    def deliveries(self, raw: bytes) -> list[ChatDelivery]:
        return webhooks.deliveries(raw)


def _new_messages(
    model: ChatModel,
    fan_uuid: str,
    seen: str | None,
    oldest: datetime,
) -> list[FanvueMessage]:
    handle: _Handle = model.client
    found: list[FanvueMessage] = []
    before = None
    answered = False
    while True:
        page = handle.client.messages(handle.creator_uuid, fan_uuid, before)
        for message in page.data:
            if message.uuid == seen:
                return found
            if message.sent_at is None:
                continue
            if answered and message.sent_at < oldest:
                return found
            found.append(message)
            answered = answered or (
                message.sender.uuid != fan_uuid and message.status == _SENT
            )
        before = page.date_filter
        if not page.data or before is None:
            return found
        if not (before.sent_before or before.received_before):
            return found


def _event(message: FanvueMessage, fan_uuid: str) -> ChatEvent | None:
    if message.sent_at is None or message.type in _NOT_A_CHAT_MESSAGE:
        return None
    if message.status and message.status != _SENT:
        return None
    return ChatEvent(
        message_id=message.uuid,
        from_fan=message.sender.uuid == fan_uuid,
        at=message.sent_at.timestamp(),
        text=(message.text or "[attachment]").replace("\x00", ""),
    )


def _fan_name(chat: FanvueChat) -> str:
    return chat.user.nickname or chat.user.display_name or chat.user.handle


def _new_reads(model: ChatModel, scope: ReadScope) -> Iterator[ChatRead]:
    handle: _Handle = model.client
    failed = 0
    for chat in handle.client.chats(handle.creator_uuid):
        last = chat.last_message
        if last is None:
            continue
        channel = webhooks.channel_of(model.model_id, chat.user.uuid)
        if scope.seen.get(channel) == last.uuid:
            if scope.thorough:
                continue
            return
        if (
            not scope.thorough
            and last.sent_at is not None
            and last.sent_at < scope.oldest
        ):
            return
        events: list[ChatEvent] = []
        if last.type not in _MASS_TYPES or channel in scope.open_channels:
            try:
                messages = _new_messages(
                    model,
                    chat.user.uuid,
                    scope.seen.get(channel),
                    scope.oldest,
                )
            except FanvueRefusedError:
                logger.warning("fanvue chat %s cannot be read", channel, exc_info=True)
                messages = []
            except FanvueError:
                logger.warning("fanvue chat %s not read", channel, exc_info=True)
                failed += 1
                if failed >= settings.chatting.sync_max_failed_chats:
                    raise
                continue
            failed = 0
            found = (_event(m, chat.user.uuid) for m in reversed(messages))
            events = [event for event in found if event is not None]
        yield ChatRead(
            channel_id=channel,
            model_username=model.username,
            fan_name=_fan_name(chat),
            last_message_id=last.uuid,
            events=events,
        )
