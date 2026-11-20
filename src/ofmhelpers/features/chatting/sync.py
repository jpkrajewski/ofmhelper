import time
from datetime import UTC, datetime, timedelta
lazy from collections.abc import Collection

from sqlalchemy.exc import SQLAlchemyError

from ofmhelpers.cache import delete_text, get_text, set_text
from ofmhelpers.config import settings
from ofmhelpers.db.stores import chats
from ofmhelpers.errors import AppError
from ofmhelpers.features.chatting import rechecks
from ofmhelpers.features.chatting.reached import remember_reached
from ofmhelpers.features.chatting.sources import source_for
from ofmhelpers.features.chatting.webhooks import (
    cover_models,
    mass_pending_key,
    models_delivering,
    note_bulk_sends,
    sync_done_key,
)
from ofmhelpers.features.notifications import service as notifications
from ofmhelpers.integrations.chat_source import ChatSourceError
from ofmhelpers.log import get_logger
from ofmhelpers.schemas.chatting import ReadScope
lazy from ofmhelpers.integrations.chat_source import ChatSource
lazy from ofmhelpers.schemas.chatting import ChatModel, ChatPlatform

logger = get_logger(__name__)


def enabled(platform: ChatPlatform) -> bool:
    source = source_for(platform)
    return source is not None and source.configured()


def _sync_model(source: ChatSource, model: ChatModel, scope: ReadScope) -> int:
    platform = source.platform
    changed = 0
    for read in source.new_reads(model, scope):
        try:
            if read.cannot_answer:
                chats.forget_open(platform, read.channel_id)
                continue
            if read.events:
                chats.record_events(platform, read)
                rechecks.plan(platform, read)
                changed += 1
            chats.mark_read(platform, read)
        except SQLAlchemyError:
            logger.exception("%s chat %s not saved", platform.value, read.channel_id)
    return changed


def sync(
    source: ChatSource, skip: Collection[str] = (), *, thorough: bool = True
) -> None:
    platform = source.platform
    started = time.monotonic()
    lookback = timedelta(seconds=settings.chatting.lookback_s)
    oldest = datetime.now(UTC) - lookback
    scope = ReadScope.since(
        chats.last_read(platform),
        oldest,
        chats.open_channels(platform),
        thorough=thorough,
    )
    reached = source.models()
    cover_models(source, reached.found)
    failed = reached.failed
    if not failed:
        remember_reached(platform, [model.username for model in reached.found])
    read = 0
    changed = 0
    for model in reached.found:
        if model.username in skip:
            continue
        read += 1
        try:
            changed += _sync_model(source, model, scope)
        except AppError:
            logger.warning(
                "%s %s not read", platform.value, model.username, exc_info=True
            )
            failed = True
    chats.forget_model_messages((oldest - lookback).timestamp())
    logger.info(
        "%s sync: %d of %d models, %d chats with news, %.1f s, failed %s",
        platform.value,
        read,
        len(reached.found),
        changed,
        time.monotonic() - started,
        failed,
    )
    if failed:
        msg = f"{platform.label} could not be read in full"
        raise ChatSourceError(msg)


def read_now(source: ChatSource, usernames: set[str]) -> set[str]:
    platform = source.platform
    oldest = datetime.now(UTC) - timedelta(seconds=settings.chatting.lookback_s)
    scope = ReadScope.since(
        chats.last_read(platform),
        oldest,
        chats.open_channels(platform),
        thorough=False,
    )
    read = set()
    for model in source.models().found:
        if model.username not in usernames:
            continue
        try:
            _sync_model(source, model, scope)
        except AppError:
            logger.warning(
                "%s %s not read", platform.value, model.username, exc_info=True
            )
            continue
        read.add(model.username)
    return read


def _models_resting(platform: ChatPlatform) -> set[str]:
    late = time.time() - settings.chatting.unanswered_after_s
    waiting = {
        wait.model_username
        for wait in chats.open_waits(before=late)
        if wait.platform == platform
    }
    return models_delivering(platform) - waiting


def _run_platform(source: ChatSource) -> None:
    platform = source.platform
    note_bulk_sends(source)
    if get_text(mass_pending_key(platform)):
        delete_text(sync_done_key(platform))
        delete_text(mass_pending_key(platform))
    if get_text(sync_done_key(platform)):
        sync(source, skip=_models_resting(platform), thorough=False)
        return
    sync(source)
    set_text(
        sync_done_key(platform), "1", int(settings.chatting.webhook_sync_interval_s)
    )


def run_when_due(platform: ChatPlatform) -> None:
    source = source_for(platform)
    if source is None:
        return
    try:
        _run_platform(source)
    finally:
        notifications.changed()
