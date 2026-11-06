"""Chat platforms' webhooks feeding /chatting, whichever platform: a fan's message or a
model's reply is stored the moment it happens, and a finished mass message, a new
subscriber or a big sale becomes a pop-up. What a platform does differently is all in
its `ChatSource`.

The destination is made by itself on every server with a public address: a sweep
makes one per owner and keeps its secret. An owner is whoever the platform ties a
destination to: one model, or an agency covering all of its models. A platform can
instead have one destination set up on its own site, covering every model with one
secret (`ChatSource.shared_secret`): then there is nothing to make. Without a public
HTTPS address (dev) there is none, and the sync does the work. A destination existing
proves nothing, so a model counts as delivering only while its replies actually arrive."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from pydantic import ValidationError
from sqlalchemy.exc import DataError

from ofmhelpers.cache import get_text, set_text
from ofmhelpers.config import settings
from ofmhelpers.db.stores import chats
from ofmhelpers.errors import AppError
from ofmhelpers.features.chatting.sources import configured_sources
from ofmhelpers.log import get_logger
from ofmhelpers.schemas.chatting import (
    ChatDelivery,
    ChatPlatform,
    ChatWebhook,
    CreatedSubscription,
    DeliveryKind,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ofmhelpers.integrations.chat_source import ChatSource
    from ofmhelpers.schemas.chatting import ChatModel

logger = get_logger(__name__)

_SHARED_DESTINATION = "shared"
# A bulk send noticed by its size, not told of by the platform.
_BULK_SEND = "bulk:"


def _public_base() -> str:
    """`OFM_CHATTING_WEBHOOK_BASE_URL`, else this server if it is public HTTPS, else
    nothing: platforms refuse anything else."""
    base = settings.chatting.webhook_base_url or settings.web.base_url
    return base.rstrip("/") if base.startswith("https://") else ""


def webhook_url(platform: ChatPlatform) -> str:
    base = _public_base()
    return f"{base}/events/{platform.value}" if base else ""


def _alive_key(platform: ChatPlatform, username: str) -> str:
    return f"chatting:webhook-alive:{platform.value}:{username}"


def sync_done_key(platform: ChatPlatform) -> str:
    """Set while the platform's full read is not due. Deleting it makes the next
    sync round a full one."""
    return f"chatting:sync-done:{platform.value}"


def mass_pending_key(platform: ChatPlatform) -> str:
    """Set by a mass message whose answers only a full read can find. Its own key: one
    arriving during a full round must make the next round full too."""
    return f"chatting:mass-pending:{platform.value}"


def models_delivering(platform: ChatPlatform) -> set[str]:
    """Models with a destination at this server's address and a reply delivered
    within `webhook_quiet_s`. A dev database copied from production holds production's
    destinations, which deliver elsewhere."""
    url = webhook_url(platform)
    if not url:
        return set()
    return {
        username
        for username, target in chats.webhook_targets(platform)
        if target == url and get_text(_alive_key(platform, username))
    }


def can_make_webhooks() -> bool:
    return bool(_public_base() and configured_sources())


def _save_rows(
    source: ChatSource, models: list[ChatModel], made: CreatedSubscription
) -> None:
    """One row per model the destination covers, so a delivery finds its model's
    name; they share the secret."""
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
    """Leaves one working destination at `url` covering `models`. Another server may
    be using the credentials, so only one at this very address is ever replaced: dev
    and production cannot take each other's."""
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
    """A destination set up on the platform's own site: nothing to make, only which
    models its one secret speaks for, kept up by every sync round. Written only when
    something changed."""
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


def ensure_destinations() -> None:
    """One sweep round: every model of every platform is covered. One owner failing
    does not stop the others, and is tried again next round."""
    for source in configured_sources():
        url = webhook_url(source.platform)
        if source.shared_secret():
            cover_models(source, source.models().found)
            continue
        mine = {w.model_id: w for w in chats.webhooks(source.platform)}
        owners: dict[str, list[ChatModel]] = {}
        for model in source.models().found:
            owners.setdefault(model.owner, []).append(model)
        for owner, models in owners.items():
            try:
                _ensure_owner(source, models, mine, url)
            except AppError:
                logger.warning(
                    "%s %s: no webhook", source.platform.value, owner, exc_info=True
                )


def note_bulk_sends(source: ChatSource) -> None:
    """A bulk send with no event of its own becomes a mass message pop-up, the one
    time, however long it takes to go out."""
    if not source.bulk_sends_unflagged:
        return
    platform = source.platform
    now = time.time()
    config = settings.chatting
    for reach in chats.models_reaching_chats(
        platform, now - config.mass_window_s, config.mass_min_chats
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


def _already_noticed(source: ChatSource, username: str) -> bool:
    """A platform's mass message event for a bulk send already shown by its size."""
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
        chats.record_events(platform, read)
    elif delivery.kind == DeliveryKind.DELETED:
        chats.forget_message(platform, delivery.message_id)
    elif delivery.kind == DeliveryKind.MASS:
        if not _already_noticed(source, username):
            chats.save_notification(platform, delivery, username)
        if not source.mass_reaches_chats:
            set_text(
                mass_pending_key(platform),
                "1",
                int(settings.chatting.webhook_sync_interval_s),
            )
    elif delivery.kind == DeliveryKind.SUBSCRIBED:
        chats.save_notification(platform, delivery, username)
    elif delivery.kind == DeliveryKind.SALE:
        # Every sale for the daily report; a big one also for the pop-up.
        chats.save_sale(platform, delivery, username)
        if (delivery.amount_cents or 0) > settings.chatting.sale_notify_cents:
            chats.save_notification(platform, delivery, username)
        else:
            logger.info(
                "%s sale of %s cents: no pop-up", platform.value, delivery.amount_cents
            )


def _shows_replies(delivery: ChatDelivery) -> bool:
    """A model's own reply arriving is what lets webhooks stand in for the sync: a
    platform may be sending other events and not these."""
    return delivery.read is not None and any(
        not event.from_fan for event in delivery.read.events
    )


def receive(source: ChatSource, raw: bytes, headers: Mapping[str, str]) -> bool:
    """One delivery. False when no destination signed it: nothing is believed. An
    event kept once is kept again harmlessly: platforms repeat until answered, and an
    answer means it is stored. A signed one that cannot be understood is logged and
    answered too: retries would not change it, and the sync catches the message. A
    delivery for a model this signature does not cover changes nothing."""
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
    except ValidationError as exc:
        # No exc_info: the error text quotes the input, which may be a fan's words.
        logger.warning(
            "%s delivery not understood: %d fields",
            platform.value,
            exc.error_count(),
        )
    except DataError:
        # Retries would not change it, and a failing address gets disabled.
        logger.warning("%s delivery not storable", platform.value)
    return True
