# Copyright (C) 2026 jpkrajewski

import time
from typing import Any
lazy from collections.abc import Mapping

from pydantic import RootModel, ValidationError
from sqlalchemy.exc import DataError

from ofmhelpers.cache import get_text, set_text
from ofmhelpers.config import settings
from ofmhelpers.db.chats import store as chats
from ofmhelpers.db.chats.schema import (
    ChatDelivery,
    ChatPlatform,
    ChatWebhook,
    CreatedSubscription,
    DeliveryKind,
)
from ofmhelpers.features.chatting import rechecks
from ofmhelpers.features.chatting.sources import configured_sources
from ofmhelpers.features.notifications import service as notifications
from ofmhelpers.features.reports.mass import tell_mass
from ofmhelpers.integrations.chat_source import ChatSourceError
from ofmhelpers.log import get_logger
lazy from ofmhelpers.db.chats.schema import ChatModel, ChatModels
lazy from ofmhelpers.integrations.chat_source import ChatSource

logger = get_logger(__name__)

_SHARED_DESTINATION = "shared"
_BULK_SEND = "bulk:"
_KEYS_MAX = 12
_NAME_MAX = 60


def _public_base() -> str:
    base = settings.chatting.webhook_base_url or settings.web.base_url
    return base.rstrip("/") if base.startswith("https://") else ""


def webhook_url(platform: ChatPlatform) -> str:
    base = _public_base()
    return f"{base}/events/{platform.value}" if base else ""


def _alive_key(platform: ChatPlatform, username: str) -> str:
    return f"chatting:webhook-alive:{platform.value}:{username}"


def sync_done_key(platform: ChatPlatform) -> str:
    return f"chatting:sync-done:{platform.value}"


def mass_pending_key(platform: ChatPlatform) -> str:
    return f"chatting:mass-pending:{platform.value}"


def models_delivering(platform: ChatPlatform) -> set[str]:
    url = webhook_url(platform)
    if not url:
        return set()
    return {
        target.model_username
        for target in chats.webhook_targets(platform)
        if target.url == url and get_text(_alive_key(platform, target.model_username))
    }


def can_make_webhooks() -> bool:
    return bool(_public_base() and configured_sources())


def _save_rows(
    source: ChatSource,
    models: list[ChatModel],
    made: CreatedSubscription,
) -> None:
    for model in models:
        chats.save_webhook(
            source.platform,
            ChatWebhook(
                model_id=model.model_id,
                model_username=model.username,
                webhook_id=made.id,
                url=made.url,
                secret=made.secret,
            ),
        )


def _ensure_owner(
    source: ChatSource,
    models: list[ChatModel],
    mine: dict[str, ChatWebhook],
    url: str,
) -> None:
    found = source.subscriptions(models[0])
    working = {
        s.id for s in found if s.url == url and set(source.events) <= set(s.events)
    }
    known = next(
        (
            mine[m.model_id]
            for m in models
            if m.model_id in mine and mine[m.model_id].webhook_id in working
        ),
        None,
    )
    if known is not None:
        made = CreatedSubscription(id=known.webhook_id, url=url, secret=known.secret)
        missing = [m for m in models if m.model_id not in mine]
        _save_rows(source, missing, made)
        return
    elsewhere = [s.url for s in found if s.url != url]
    if elsewhere:
        logger.warning(
            "%s %s already delivers to %s: remove it there to deliver to %s",
            source.platform.value,
            models[0].owner,
            elsewhere[0],
            url,
        )
        return
    for subscription in found:
        source.unsubscribe(models[0], subscription.id)
    made = source.subscribe(models[0], url)
    _save_rows(source, models, made)
    logger.info(
        "%s %s now delivers to %s for %d models",
        source.platform.value,
        models[0].owner,
        made.url,
        len(models),
    )


def cover_models(source: ChatSource, models: list[ChatModel]) -> None:
    secret = source.shared_secret()
    if not secret:
        return
    url = webhook_url(source.platform)
    known = {w.model_id: w for w in chats.webhooks(source.platform)}
    stale = [
        model
        for model in models
        if (row := known.get(model.model_id)) is None
        or (row.secret, row.url, row.model_username) != (secret, url, model.username)
    ]
    made = CreatedSubscription(id=_SHARED_DESTINATION, url=url, secret=secret)
    _save_rows(source, stale, made)


def drop_unreached(source: ChatSource, reached: ChatModels) -> None:
    if reached.failed or reached.unread or not reached.found:
        return
    dropped = chats.drop_webhooks_except(
        source.platform,
        [model.model_id for model in reached.found],
    )
    for row in dropped:
        logger.warning(
            "%s: webhook row of %s removed, no account or "
            "key reaches it; its subscription stays live",
            source.platform.value,
            row.model_username,
        )


def ensure_destinations() -> None:
    for source in configured_sources():
        url = webhook_url(source.platform)
        reached = source.models()
        drop_unreached(source, reached)
        if source.shared_secret():
            cover_models(source, reached.found)
            continue
        mine = {w.model_id: w for w in chats.webhooks(source.platform)}
        owners: dict[str, list[ChatModel]] = {}
        for model in reached.found:
            owners.setdefault(model.owner, []).append(model)
        for owner, models in owners.items():
            try:
                _ensure_owner(source, models, mine, url)
            except ChatSourceError:
                logger.warning(
                    "%s %s: no webhook",
                    source.platform.value,
                    owner,
                    exc_info=True,
                )


def note_bulk_sends(source: ChatSource) -> None:
    if not source.bulk_sends_unflagged:
        return
    platform = source.platform
    now = time.time()
    config = settings.chatting
    for reach in chats.models_reaching_chats(
        platform,
        now - config.mass_window_s,
        config.mass_min_chats,
    ):
        username = reach.model_username
        if chats.recent_mass_messages(platform, username, now - config.mass_gap_s):
            continue
        chats.save_notification(
            platform,
            ChatDelivery(
                kind=DeliveryKind.MASS,
                model_id="",
                event_id=f"{_BULK_SEND}{username}:{int(reach.started_at)}",
                at=reach.started_at,
            ),
            username,
        )
        logger.info("%s bulk send by %s", platform.value, username)
        tell_mass()


def _already_noticed(source: ChatSource, username: str) -> bool:
    if not source.bulk_sends_unflagged:
        return False
    since = time.time() - settings.chatting.mass_gap_s
    return any(
        event.startswith(_BULK_SEND)
        for event in chats.recent_mass_messages(source.platform, username, since)
    )


def _keep(source: ChatSource, delivery: ChatDelivery, webhook: ChatWebhook) -> None:
    platform = source.platform
    username = webhook.model_username
    if delivery.kind == DeliveryKind.MESSAGE and delivery.read:
        read = delivery.read.model_copy(update={"model_username": username})
        if not chats.record_events(platform, read):
            logger.warning(
                "%s delivery signed for %s names another model's chat: ignored",
                platform.value,
                username,
            )
            return
        rechecks.plan(platform, read)
    elif delivery.kind == DeliveryKind.DELETED:
        chats.forget_message(platform, delivery.message_id, username)
    elif delivery.kind == DeliveryKind.MASS:
        if not _already_noticed(source, username):
            chats.save_notification(platform, delivery, username)
            tell_mass()
        if not source.mass_reaches_chats:
            set_text(
                mass_pending_key(platform),
                "1",
                int(settings.chatting.webhook_sync_interval_s),
            )
    elif delivery.kind == DeliveryKind.SUBSCRIBED:
        chats.save_notification(platform, delivery, username)
    elif delivery.kind == DeliveryKind.SALE:
        chats.save_sale(platform, delivery, username)
        if (delivery.amount_cents or 0) > settings.chatting.sale_notify_cents:
            chats.save_notification(platform, delivery, username)
        else:
            logger.info(
                "%s sale of %s cents: no pop-up",
                platform.value,
                delivery.amount_cents,
            )


def _shows_replies(delivery: ChatDelivery) -> bool:
    return delivery.read is not None and any(
        not event.from_fan for event in delivery.read.events
    )


class _Top(RootModel[dict[str, Any]]): ...


def _named(raw: bytes) -> str:
    try:
        top = _Top.model_validate_json(raw).root
    except ValidationError:  # silent: told as "not a JSON object"
        return "not a JSON object"
    kind = top.get("type")
    kind = kind[:_NAME_MAX] if isinstance(kind, str) else "-"
    keys = ",".join(sorted(k[:_NAME_MAX] for k in top)[:_KEYS_MAX])
    return f"type {kind}, keys {keys}"


def _failed_fields(exc: ValidationError) -> str:
    errors = exc.errors(include_input=False, include_url=False, include_context=False)
    return "; ".join(
        f"{'.'.join(str(part) for part in e['loc']) or '-'}: {e['type']}"
        for e in errors[:_KEYS_MAX]
    )


def receive(source: ChatSource, raw: bytes, headers: Mapping[str, str]) -> bool:
    if source.answers_unsigned(raw, headers):
        return True
    platform = source.platform
    signed = {
        w.model_id: w
        for w in chats.webhooks(platform)
        if source.verified(w.secret, raw, headers)
    }
    shared = source.shared_secret()
    if not signed and not (shared and source.verified(shared, raw, headers)):
        logger.warning("%s delivery refused: signature", platform.value)
        return False
    try:
        for delivery in source.deliveries(raw):
            webhook = signed.get(delivery.model_id)
            if webhook is None:
                logger.warning(
                    "%s %s delivery for an unknown model: %s",
                    platform.value,
                    delivery.kind.value,
                    delivery.model_id,
                )
                continue
            _keep(source, delivery, webhook)
            logger.info(
                "%s %s delivery for %s",
                platform.value,
                delivery.kind.value,
                webhook.model_username,
            )
            if _shows_replies(delivery):
                set_text(
                    _alive_key(platform, webhook.model_username),
                    "1",
                    settings.chatting.webhook_quiet_s,
                )
    except ValidationError as exc:  # silent: may quote a fan's words
        logger.warning(
            "%s delivery not understood: %d fields (%s) of %s",
            platform.value,
            exc.error_count(),
            _failed_fields(exc),
            _named(raw),
        )
    except DataError:  # silent: the database's text may quote a fan's words
        logger.warning("%s delivery not storable", platform.value)
    notifications.changed()
    return True
