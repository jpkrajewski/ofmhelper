"""Fanvue as a chat source (`integrations/chat_source.py`): accounts connected through
OAuth, with tokens kept outside this package (integrations do not touch the database,
so the keeper is handed in)."""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING, NamedTuple, Protocol

from ofmhelpers.config import settings
from ofmhelpers.integrations.fanvue import webhooks
from ofmhelpers.integrations.fanvue.client import FanvueClient
from ofmhelpers.integrations.fanvue.errors import FanvueError, FanvueRefusedError
from ofmhelpers.log import get_logger
from ofmhelpers.schemas.chatting import (
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

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping
    from datetime import datetime

    from ofmhelpers.integrations.fanvue.models import FanvueChat, FanvueMessage

logger = get_logger(__name__)

# `creator.message.mass_sent` fires for both (docs: creator/messages).
_MASS_TYPES = frozenset({"BROADCAST", "GHOST_PROMOTION"})
_SENT = "SENT"
# Fanvue's own, not the model's words or the fan's.
_NOT_A_CHAT_MESSAGE = frozenset({"MARKETING_KYC"})


class TokenKeeper(Protocol):
    def accounts(self) -> list[ChatAccount]: ...

    def access_token(self, account_id: str) -> str:
        """A live token. Raises `FanvueAuthError` when someone must connect again."""
        ...


class _Handle(NamedTuple):
    client: FanvueClient
    # Set for an agency acting for a creator: its calls go through /creators/<uuid>.
    creator_uuid: str | None


class FanvueSource:
    platform = ChatPlatform.FANVUE
    # `creator.message.mass_sent` fires once and names no recipients, so what it
    # answered is only found by reading those chats.
    mass_reaches_chats = False
    events = webhooks.EVENTS

    def __init__(self, tokens: TokenKeeper) -> None:
        self._tokens = tokens

    def configured(self) -> bool:
        return bool(settings.fanvue.client_id and settings.fanvue.client_secret)

    def models(self) -> ChatModels:
        found: dict[str, ChatModel] = {}
        failed = False
        for account in self._tokens.accounts():
            if account.needs_reconnect:
                continue
            client = FanvueClient(
                partial(self._tokens.access_token, account.account_id)
            )
            if not account.is_agency:
                # Their own connection wins over an agency's: only it can have a webhook.
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
                # Lost its seat, say: a full round every minute would not bring it back.
                logger.warning("fanvue agency %s refused", account.username)
            except FanvueError:
                logger.warning(
                    "fanvue agency %s not read", account.username, exc_info=True
                )
                failed = True
        return ChatModels(found=list(found.values()), failed=failed)

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
        """On the token's own account. Fanvue answers 403 to an agency's token (seen
        2026-11-05), which is logged; those models stay on the sync until the creator
        connects with their own login."""
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
    model: ChatModel, fan_uuid: str, seen: str | None, oldest: datetime
) -> list[FanvueMessage]:
    """The chat's messages after `seen`, newest first. History stops at `oldest`, but
    a fan's messages nobody has answered yet are read however old."""
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
            # An unsent mass message sits in the thread too, and answers nobody.
            answered = answered or (
                message.sender.uuid != fan_uuid and message.status == _SENT
            )
        before = page.date_filter
        if not page.data or before is None:
            return found
        if not (before.sent_before or before.received_before):
            return found


def _event(message: FanvueMessage, fan_uuid: str) -> ChatEvent | None:
    """None for what does not count: unsent or moderated, or Fanvue's own."""
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
    """Latest activity first, so a quick round stops at the first chat with nothing
    new, and a thorough one at the first past `oldest`. A mass message changes every
    chat it reached and Fanvue allows 200 calls a minute, so a chat it was last in is
    opened only if a fan there is waiting: that is the only one it can have answered."""
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
        if last.sent_at is not None and last.sent_at < scope.oldest:
            return
        events: list[ChatEvent] = []
        if last.type not in _MASS_TYPES or channel in scope.open_channels:
            try:
                messages = _new_messages(
                    model, chat.user.uuid, scope.seen.get(channel), scope.oldest
                )
            except FanvueRefusedError:
                # Seen: 400 "Invalid user UUID". Not asked again until the chat changes.
                logger.warning("fanvue chat %s cannot be read", channel)
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
