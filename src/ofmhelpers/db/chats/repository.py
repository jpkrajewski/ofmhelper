# Copyright (C) 2026 jpkrajewski

lazy from collections.abc import Callable

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
lazy from sqlalchemy.orm import Session

from ofmhelpers.db.models import (
    ChatAccountRow,
    ChatFanMessageRow,
    ChatModelMessageRow,
    ChatNotificationRow,
    ChatSaleRow,
    ChatThreadRow,
    ChatWebhookRow,
)
from ofmhelpers.db.session import session_scope
lazy from ofmhelpers.db.chats.schema import (
    ChatAccount,
    ChatEvent,
    ChatPosition,
    ChatRead,
    ChatTokens,
    ChatWebhook,
    FanMessage,
    ModelReach,
    ModelTotal,
    OpenWait,
    StoredNotification,
    WebhookTarget,
)


class ChatRepository:
    def last_read(self, platform: str) -> list[ChatPosition]:
        with session_scope() as s:
            rows = s.execute(
                select(ChatThreadRow.channel_id, ChatThreadRow.last_message_id).where(
                    ChatThreadRow.platform == platform,
                ),
            )
            return [ChatPosition.model_validate(dict(row)) for row in rows.mappings()]

    def record_events(self, platform: str, read: ChatRead) -> bool:
        with session_scope() as s:
            s.execute(
                select(
                    func.pg_advisory_xact_lock(
                        func.hashtextextended(f"chat:{platform}:{read.channel_id}", 0),
                    ),
                ),
            )
            owner = s.execute(
                select(ChatFanMessageRow.model_username)
                .where(
                    ChatFanMessageRow.platform == platform,
                    ChatFanMessageRow.channel_id == read.channel_id,
                )
                .limit(1),
            ).scalar_one_or_none()
            if owner is not None and owner != read.model_username:
                return False
            for event in read.events:
                if event.from_fan:
                    self._keep_fan_message(s, platform, read, event)
                else:
                    self._keep_model_message(s, platform, read, event)
            return True

    def _keep_fan_message(
        self,
        s: Session,
        platform: str,
        read: ChatRead,
        event: ChatEvent,
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
            .on_conflict_do_nothing(),
        )

    def _keep_model_message(
        self,
        s: Session,
        platform: str,
        read: ChatRead,
        event: ChatEvent,
    ) -> None:
        s.execute(
            insert(ChatModelMessageRow)
            .values(
                platform=platform,
                message_id=event.message_id,
                channel_id=read.channel_id,
                sent_at=event.at,
            )
            .on_conflict_do_nothing(),
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
            .values(replied_at=event.at),
        )

    def forget_model_messages(self, before: float) -> None:
        with session_scope() as s:
            s.execute(
                delete(ChatModelMessageRow).where(ChatModelMessageRow.sent_at < before),
            )

    def mark_read(self, platform: str, read: ChatRead) -> None:
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
                ),
            )

    def forget_open(self, platform: str, channel_id: str) -> None:
        with session_scope() as s:
            s.execute(
                delete(ChatFanMessageRow).where(
                    ChatFanMessageRow.platform == platform,
                    ChatFanMessageRow.channel_id == channel_id,
                    ChatFanMessageRow.replied_at.is_(None),
                ),
            )

    def forget_message(
        self,
        platform: str,
        message_id: str,
        model_username: str,
    ) -> None:
        with session_scope() as s:
            s.execute(
                delete(ChatFanMessageRow).where(
                    ChatFanMessageRow.platform == platform,
                    ChatFanMessageRow.message_id == message_id,
                    ChatFanMessageRow.model_username == model_username,
                ),
            )

    def models(self, platform: str) -> list[str]:
        with session_scope() as s:
            return list(
                s.execute(
                    select(ChatThreadRow.model_username)
                    .where(ChatThreadRow.platform == platform)
                    .distinct()
                    .order_by(ChatThreadRow.model_username),
                ).scalars(),
            )

    def fan_messages(
        self,
        platform: str,
        username: str,
        since: float,
    ) -> list[FanMessage]:
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
                .order_by(ChatFanMessageRow.sent_at.desc()),
            )
            return [FanMessage.model_validate(dict(row)) for row in rows.mappings()]

    def open_waits(self, before: float) -> list[OpenWait]:
        started_at = func.min(ChatFanMessageRow.sent_at).label("started_at")
        with session_scope() as s:
            rows = s.execute(
                select(
                    ChatFanMessageRow.platform,
                    ChatFanMessageRow.model_username,
                    ChatFanMessageRow.channel_id,
                    started_at,
                    func.max(ChatFanMessageRow.fan_name).label("fan_name"),
                )
                .where(ChatFanMessageRow.replied_at.is_(None))
                .group_by(
                    ChatFanMessageRow.platform,
                    ChatFanMessageRow.model_username,
                    ChatFanMessageRow.channel_id,
                )
                .having(started_at < before),
            )
            return [OpenWait.model_validate(dict(row)) for row in rows.mappings()]

    def save_notification(  # noqa: PLR0913, PLR0917
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
                .on_conflict_do_nothing(),
            )

    def save_sale(  # noqa: PLR0913, PLR0917
        self,
        platform: str,
        event_id: str,
        username: str,
        gross_cents: int,
        currency: str,
        source: str,
        at: float,
    ) -> None:
        with session_scope() as s:
            s.execute(
                insert(ChatSaleRow)
                .values(
                    platform=platform,
                    event_id=event_id,
                    model_username=username,
                    gross_cents=gross_cents,
                    currency=currency,
                    source=source,
                    at=at,
                )
                .on_conflict_do_nothing(),
            )

    def sales_between(self, since: float, until: float) -> list[ModelTotal]:
        with session_scope() as s:
            rows = s.execute(
                select(
                    ChatSaleRow.platform,
                    ChatSaleRow.model_username,
                    func.count().label("count"),
                    func.sum(ChatSaleRow.gross_cents).label("cents"),
                )
                .where(ChatSaleRow.at >= since, ChatSaleRow.at < until)
                .group_by(ChatSaleRow.platform, ChatSaleRow.model_username),
            )
            return [ModelTotal.model_validate(dict(row)) for row in rows.mappings()]

    def counts_between(self, kind: str, since: float, until: float) -> list[ModelTotal]:
        with session_scope() as s:
            rows = s.execute(
                select(
                    ChatNotificationRow.platform,
                    ChatNotificationRow.model_username,
                    func.count().label("count"),
                )
                .where(
                    ChatNotificationRow.kind == kind,
                    ChatNotificationRow.at >= since,
                    ChatNotificationRow.at < until,
                )
                .group_by(
                    ChatNotificationRow.platform,
                    ChatNotificationRow.model_username,
                ),
            )
            return [ModelTotal.model_validate(dict(row)) for row in rows.mappings()]

    def models_reaching_chats(
        self,
        platform: str,
        since: float,
        min_chats: int,
    ) -> list[ModelReach]:
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
                .having(reached >= min_chats),
            )
            return [ModelReach.model_validate(dict(row)) for row in rows.mappings()]

    def recent_events(
        self,
        platform: str,
        username: str,
        kind: str,
        since: float,
    ) -> list[str]:
        with session_scope() as s:
            return list(
                s.execute(
                    select(ChatNotificationRow.event_id).where(
                        ChatNotificationRow.platform == platform,
                        ChatNotificationRow.model_username == username,
                        ChatNotificationRow.kind == kind,
                        ChatNotificationRow.at >= since,
                    ),
                ).scalars(),
            )

    def notifications(self, since: float) -> list[StoredNotification]:
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
                    ChatNotificationRow.at,
                )
                .where(ChatNotificationRow.at >= since)
                .order_by(ChatNotificationRow.at.desc()),
            )
            return [
                StoredNotification.model_validate(dict(row)) for row in rows.mappings()
            ]

    def open_channels(self, platform: str) -> set[str]:
        with session_scope() as s:
            return set(
                s.execute(
                    select(ChatFanMessageRow.channel_id)
                    .where(
                        ChatFanMessageRow.platform == platform,
                        ChatFanMessageRow.replied_at.is_(None),
                    )
                    .distinct(),
                ).scalars(),
            )

    def accounts(self, platform: str, connected_from: str) -> list[ChatAccount]:
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
                .order_by(ChatAccountRow.username),
            )
            return [ChatAccount.model_validate(dict(row)) for row in rows.mappings()]

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
            "access_token": tokens.access_token.get_secret_value(),
            "refresh_token": tokens.refresh_token.get_secret_value(),
            "expires_at": tokens.expires_at,
        }
        with session_scope() as s:
            s.execute(
                insert(ChatAccountRow)
                .values(platform=platform, account_id=account.account_id, **values)
                .on_conflict_do_update(
                    index_elements=["platform", "account_id"],
                    set_=values,
                ),
            )

    def fresh_token(
        self,
        platform: str,
        account_id: str,
        renew: Callable[[str], ChatTokens | None],
        valid_until: float,
        connected_from: str,
    ) -> str | None:
        with session_scope() as s:
            s.execute(
                select(
                    func.pg_advisory_xact_lock(
                        func.hashtextextended(f"token:{platform}:{account_id}", 0),
                    ),
                ),
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
            row.access_token = tokens.access_token.get_secret_value()
            row.refresh_token = tokens.refresh_token.get_secret_value()
            row.expires_at = tokens.expires_at
            return tokens.access_token.get_secret_value()

    def webhooks(self, platform: str) -> list[ChatWebhook]:
        with session_scope() as s:
            rows = s.execute(
                select(
                    ChatWebhookRow.model_id,
                    ChatWebhookRow.model_username,
                    ChatWebhookRow.webhook_id,
                    ChatWebhookRow.url,
                    ChatWebhookRow.secret,
                ).where(ChatWebhookRow.platform == platform),
            )
            return [ChatWebhook.model_validate(dict(row)) for row in rows.mappings()]

    def webhook_targets(self, platform: str) -> list[WebhookTarget]:
        with session_scope() as s:
            rows = s.execute(
                select(ChatWebhookRow.model_username, ChatWebhookRow.url).where(
                    ChatWebhookRow.platform == platform,
                ),
            )
            return [WebhookTarget.model_validate(dict(row)) for row in rows.mappings()]

    def drop_webhooks_except(
        self,
        platform: str,
        model_ids: list[str],
    ) -> list[ChatWebhook]:
        if not model_ids:
            return []
        with session_scope() as s:
            dropped = s.execute(
                delete(ChatWebhookRow)
                .where(
                    ChatWebhookRow.platform == platform,
                    ChatWebhookRow.model_id.not_in(model_ids),
                )
                .returning(
                    ChatWebhookRow.model_id,
                    ChatWebhookRow.model_username,
                    ChatWebhookRow.webhook_id,
                    ChatWebhookRow.url,
                    ChatWebhookRow.secret,
                ),
            )
            return [ChatWebhook.model_validate(dict(row)) for row in dropped.mappings()]

    def save_webhook(self, platform: str, webhook: ChatWebhook) -> None:
        values = {
            "model_username": webhook.model_username,
            "webhook_id": webhook.webhook_id,
            "url": webhook.url,
            "secret": webhook.secret.get_secret_value(),
        }
        with session_scope() as s:
            s.execute(
                insert(ChatWebhookRow)
                .values(platform=platform, model_id=webhook.model_id, **values)
                .on_conflict_do_update(
                    index_elements=["platform", "model_id"],
                    set_=values,
                ),
            )
