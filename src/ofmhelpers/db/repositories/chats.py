"""/chatting's history: each chat's read position (`chat_threads`), every fan
message with when it was answered (`chat_fan_messages`), when the model wrote
(`chat_model_messages`) and the webhook destinations feeding them
(`chat_webhooks`). Nothing here is cached: messages
arrive any second and the page wants what arrived, and a destination holds a
decrypted secret, which must never reach Redis."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert

from ofmhelpers.db.models import (
    ChatAccountRow,
    ChatFanMessageRow,
    ChatModelMessageRow,
    ChatNotificationRow,
    ChatThreadRow,
    ChatWebhookRow,
)
from ofmhelpers.db.session import session_scope

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.orm import Session

    from ofmhelpers.schemas.chatting import (
        ChatAccount,
        ChatEvent,
        ChatRead,
        ChatTokens,
        ChatWebhook,
    )


class ChatRepository:
    def last_read(self, platform: str) -> dict[str, str]:
        """{channel id: the newest message id the sync has read in it}."""
        with session_scope() as s:
            rows = s.execute(
                select(ChatThreadRow.channel_id, ChatThreadRow.last_message_id).where(
                    ChatThreadRow.platform == platform
                )
            )
            return dict(rows.tuples().all())

    def record_events(self, platform: str, read: ChatRead) -> None:
        """A chat's messages. Whatever order they come in, in however many
        calls, a fan message ends up answered by the first model message at or
        after it. One chat is written one call at a time, so a fan message
        and its reply landing together each see the other; other chats do
        not wait. Safe to repeat: a webhook delivered twice, or the sync
        reading what one delivered, changes nothing."""
        with session_scope() as s:
            s.execute(
                select(
                    func.pg_advisory_xact_lock(
                        func.hashtextextended(f"chat:{platform}:{read.channel_id}", 0)
                    )
                )
            )
            for event in read.events:
                if event.from_fan:
                    self._keep_fan_message(s, platform, read, event)
                else:
                    self._keep_model_message(s, platform, read, event)

    def _keep_fan_message(
        self, s: Session, platform: str, read: ChatRead, event: ChatEvent
    ) -> None:
        s.execute(
            insert(ChatFanMessageRow)
            .values(
                platform=platform,
                message_id=event.message_id,
                channel_id=read.channel_id,
                model_username=read.model_username,
                fan_name=read.fan_name,
                text=event.text,
                sent_at=event.at,
                replied_at=select(func.min(ChatModelMessageRow.sent_at))
                .where(
                    ChatModelMessageRow.platform == platform,
                    ChatModelMessageRow.channel_id == read.channel_id,
                    ChatModelMessageRow.sent_at >= event.at,
                )
                .scalar_subquery(),
            )
            .on_conflict_do_nothing()
        )

    def _keep_model_message(
        self, s: Session, platform: str, read: ChatRead, event: ChatEvent
    ) -> None:
        s.execute(
            insert(ChatModelMessageRow)
            .values(
                platform=platform,
                message_id=event.message_id,
                channel_id=read.channel_id,
                sent_at=event.at,
            )
            .on_conflict_do_nothing()
        )
        s.execute(
            update(ChatFanMessageRow)
            .where(
                ChatFanMessageRow.platform == platform,
                ChatFanMessageRow.channel_id == read.channel_id,
                ChatFanMessageRow.sent_at <= event.at,
                or_(
                    ChatFanMessageRow.replied_at.is_(None),
                    ChatFanMessageRow.replied_at > event.at,
                ),
            )
            .values(replied_at=event.at)
        )

    def forget_model_messages(self, before: float) -> None:
        """Only a late delivery needs these, so they are not kept for long."""
        with session_scope() as s:
            s.execute(
                delete(ChatModelMessageRow).where(ChatModelMessageRow.sent_at < before)
            )

    def mark_read(self, platform: str, read: ChatRead) -> None:
        """How far the sync has read this chat. Webhooks never move it, so the
        sync still reads what they delivered and repairs one that came out of
        order or never came."""
        with session_scope() as s:
            s.execute(
                insert(ChatThreadRow)
                .values(
                    platform=platform,
                    channel_id=read.channel_id,
                    model_username=read.model_username,
                    last_message_id=read.last_message_id,
                )
                .on_conflict_do_update(
                    index_elements=["platform", "channel_id"],
                    set_={"last_message_id": read.last_message_id},
                )
            )

    def forget_open(self, platform: str, channel_id: str) -> None:
        """A fan who blocked or left cannot be answered: nothing there waits."""
        with session_scope() as s:
            s.execute(
                delete(ChatFanMessageRow).where(
                    ChatFanMessageRow.platform == platform,
                    ChatFanMessageRow.channel_id == channel_id,
                    ChatFanMessageRow.replied_at.is_(None),
                )
            )

    def forget_message(self, platform: str, message_id: str) -> None:
        """A message its sender deleted: nothing to answer, or to count as one."""
        with session_scope() as s:
            s.execute(
                delete(ChatFanMessageRow).where(
                    ChatFanMessageRow.platform == platform,
                    ChatFanMessageRow.message_id == message_id,
                )
            )

    def models(self, platform: str) -> list[str]:
        """Every model with a chat read on `platform`, by name."""
        with session_scope() as s:
            return list(
                s.execute(
                    select(ChatThreadRow.model_username)
                    .where(ChatThreadRow.platform == platform)
                    .distinct()
                    .order_by(ChatThreadRow.model_username)
                ).scalars()
            )

    def fan_messages(self, platform: str, username: str, since: float) -> list[dict]:
        """A model's fan messages sent or answered after `since`, and any still
        waiting however old, newest first."""
        with session_scope() as s:
            rows = s.execute(
                select(
                    ChatFanMessageRow.channel_id,
                    ChatFanMessageRow.fan_name,
                    ChatFanMessageRow.text,
                    ChatFanMessageRow.sent_at,
                    ChatFanMessageRow.replied_at,
                )
                .where(
                    ChatFanMessageRow.platform == platform,
                    ChatFanMessageRow.model_username == username,
                    or_(
                        ChatFanMessageRow.sent_at >= since,
                        ChatFanMessageRow.replied_at.is_(None),
                        ChatFanMessageRow.replied_at >= since,
                    ),
                )
                .order_by(ChatFanMessageRow.sent_at.desc())
            )
            return [dict(row) for row in rows.mappings()]

    def open_waits(self, before: float) -> list[dict]:
        """Every platform's chats whose fan has waited since before `before`, however
        long ago. A chat's unanswered messages are one wait, from the first."""
        started_at = func.min(ChatFanMessageRow.sent_at).label("started_at")
        with session_scope() as s:
            rows = s.execute(
                select(
                    ChatFanMessageRow.platform,
                    ChatFanMessageRow.model_username,
                    ChatFanMessageRow.channel_id,
                    started_at,
                )
                .where(ChatFanMessageRow.replied_at.is_(None))
                .group_by(
                    ChatFanMessageRow.platform,
                    ChatFanMessageRow.model_username,
                    ChatFanMessageRow.channel_id,
                )
                .having(started_at < before)
            )
            return [dict(row) for row in rows.mappings()]

    def save_notification(
        self,
        platform: str,
        event_id: str,
        username: str,
        kind: str,
        amount_cents: int | None,
        currency: str | None,
        detail: str,
        at: float,
    ) -> None:
        """Once per event, however often the platform repeats it."""
        with session_scope() as s:
            s.execute(
                insert(ChatNotificationRow)
                .values(
                    platform=platform,
                    event_id=event_id,
                    model_username=username,
                    kind=kind,
                    amount_cents=amount_cents,
                    currency=currency,
                    detail=detail,
                    at=at,
                )
                .on_conflict_do_nothing()
            )

    def models_reaching_chats(
        self, platform: str, since: float, min_chats: int
    ) -> list[dict]:
        """Who wrote in at least `min_chats` chats since `since`, and from when."""
        reached = func.count(ChatModelMessageRow.channel_id.distinct())
        with session_scope() as s:
            rows = s.execute(
                select(
                    ChatThreadRow.model_username,
                    func.min(ChatModelMessageRow.sent_at).label("started_at"),
                )
                .join(
                    ChatThreadRow,
                    and_(
                        ChatThreadRow.platform == ChatModelMessageRow.platform,
                        ChatThreadRow.channel_id == ChatModelMessageRow.channel_id,
                    ),
                )
                .where(
                    ChatModelMessageRow.platform == platform,
                    ChatModelMessageRow.sent_at >= since,
                )
                .group_by(ChatThreadRow.model_username)
                .having(reached >= min_chats)
            )
            return [dict(row) for row in rows.mappings()]

    def recent_events(
        self, platform: str, username: str, kind: str, since: float
    ) -> list[str]:
        """The ids of a model's notifications of this kind since `since`."""
        with session_scope() as s:
            return list(
                s.execute(
                    select(ChatNotificationRow.event_id).where(
                        ChatNotificationRow.platform == platform,
                        ChatNotificationRow.model_username == username,
                        ChatNotificationRow.kind == kind,
                        ChatNotificationRow.at >= since,
                    )
                ).scalars()
            )

    def notifications(self, since: float) -> list[dict]:
        """Every platform's since `since`, newest first."""
        with session_scope() as s:
            rows = s.execute(
                select(
                    ChatNotificationRow.platform,
                    ChatNotificationRow.event_id,
                    ChatNotificationRow.model_username,
                    ChatNotificationRow.kind,
                    ChatNotificationRow.amount_cents,
                    ChatNotificationRow.currency,
                    ChatNotificationRow.detail,
                )
                .where(ChatNotificationRow.at >= since)
                .order_by(ChatNotificationRow.at.desc())
            )
            return [dict(row) for row in rows.mappings()]

    def open_channels(self, platform: str) -> set[str]:
        """Chats with a fan waiting, however briefly."""
        with session_scope() as s:
            return set(
                s.execute(
                    select(ChatFanMessageRow.channel_id)
                    .where(
                        ChatFanMessageRow.platform == platform,
                        ChatFanMessageRow.replied_at.is_(None),
                    )
                    .distinct()
                ).scalars()
            )

    def accounts(self, platform: str, connected_from: str) -> list[dict]:
        """Who connected on this server, no token read."""
        with session_scope() as s:
            rows = s.execute(
                select(
                    ChatAccountRow.account_id,
                    ChatAccountRow.username,
                    ChatAccountRow.is_agency,
                    ChatAccountRow.needs_reconnect,
                )
                .where(
                    ChatAccountRow.platform == platform,
                    ChatAccountRow.connected_from == connected_from,
                )
                .order_by(ChatAccountRow.username)
            )
            return [dict(row) for row in rows.mappings()]

    def save_account(
        self,
        platform: str,
        account: ChatAccount,
        tokens: ChatTokens,
        connected_from: str,
    ) -> None:
        values = {
            "connected_from": connected_from,
            "username": account.username,
            "is_agency": account.is_agency,
            "needs_reconnect": False,
            "access_token": tokens.access_token,
            "refresh_token": tokens.refresh_token,
            "expires_at": tokens.expires_at,
        }
        with session_scope() as s:
            s.execute(
                insert(ChatAccountRow)
                .values(platform=platform, account_id=account.account_id, **values)
                .on_conflict_do_update(
                    index_elements=["platform", "account_id"], set_=values
                )
            )

    def fresh_token(
        self,
        platform: str,
        account_id: str,
        renew: Callable[[str], ChatTokens | None],
        valid_until: float,
        connected_from: str,
    ) -> str | None:
        """A token good until `valid_until`, renewing if needed; None when someone
        must connect again. A refresh token works once and reusing it kills the whole
        authorisation, so the lock is held across the call, and the new pair is saved
        in the same transaction: two callers cannot both spend the same one, and
        another server's stays unspent."""
        with session_scope() as s:
            s.execute(
                select(
                    func.pg_advisory_xact_lock(
                        func.hashtextextended(f"token:{platform}:{account_id}", 0)
                    )
                )
            )
            row = s.get(ChatAccountRow, (platform, account_id))
            if (
                row is None
                or row.needs_reconnect
                or row.connected_from != connected_from
            ):
                return None
            if row.expires_at > valid_until:
                return row.access_token
            tokens = renew(row.refresh_token)
            if tokens is None:
                row.needs_reconnect = True
                return None
            row.access_token = tokens.access_token
            row.refresh_token = tokens.refresh_token
            row.expires_at = tokens.expires_at
            return tokens.access_token

    def webhooks(self, platform: str) -> list[dict]:
        """Every destination on `platform`, secrets decrypted."""
        with session_scope() as s:
            rows = s.execute(
                select(
                    ChatWebhookRow.model_id,
                    ChatWebhookRow.model_username,
                    ChatWebhookRow.webhook_id,
                    ChatWebhookRow.url,
                    ChatWebhookRow.secret,
                ).where(ChatWebhookRow.platform == platform)
            )
            return [dict(row) for row in rows.mappings()]

    def webhook_targets(self, platform: str) -> list[tuple[str, str]]:
        """Each destination's (model, url), no secret read."""
        with session_scope() as s:
            rows = s.execute(
                select(ChatWebhookRow.model_username, ChatWebhookRow.url).where(
                    ChatWebhookRow.platform == platform
                )
            )
            return list(rows.tuples().all())

    def save_webhook(self, platform: str, webhook: ChatWebhook) -> None:
        values = webhook.model_dump(exclude={"model_id"})
        with session_scope() as s:
            s.execute(
                insert(ChatWebhookRow)
                .values(platform=platform, model_id=webhook.model_id, **values)
                .on_conflict_do_update(
                    index_elements=["platform", "model_id"], set_=values
                )
            )
