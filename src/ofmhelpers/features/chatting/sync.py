"""The sync behind /chatting's history, whichever platform: new messages into the
store, for what webhooks missed.

A model whose webhook is delivering is read rarely. One with no webhook, one gone
quiet, or one with a fan left waiting is read every minute: a dead webhook costs
minutes, never an hour, and nobody is called unanswered on what a webhook did not say.

A failed round (a platform answering 500) is logged by the sweep and costs nothing:
what was read is saved, and the next round carries on from there. A model failing does
not stop the others, and each platform has its own sweep, so one failing, slow or
hanging does not hold up the next.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy.exc import SQLAlchemyError

from ofmhelpers.cache import delete_text, get_text, set_text
from ofmhelpers.config import settings
from ofmhelpers.db.stores import chats
from ofmhelpers.errors import AppError
from ofmhelpers.features.chatting.sources import source_for
from ofmhelpers.features.chatting.webhooks import (
    cover_models,
    mass_pending_key,
    models_delivering,
    note_bulk_sends,
    sync_done_key,
)
from ofmhelpers.integrations.chat_source import ChatSourceError
from ofmhelpers.log import get_logger
from ofmhelpers.schemas.chatting import ReadScope

if TYPE_CHECKING:
    from collections.abc import Collection

    from ofmhelpers.integrations.chat_source import ChatSource
    from ofmhelpers.schemas.chatting import ChatModel, ChatPlatform

logger = get_logger(__name__)


def enabled(platform: ChatPlatform) -> bool:
    source = source_for(platform)
    return source is not None and source.configured()


def _sync_model(source: ChatSource, model: ChatModel, scope: ReadScope) -> int:
    """Saved chat by chat, and how many changed. A chat that cannot be saved stays
    unread and comes back next round, and does not stop the ones behind it."""
    platform = source.platform
    changed = 0
    for read in source.new_reads(model, scope):
        try:
            if read.cannot_answer:
                chats.forget_open(platform, read.channel_id)
                continue
            if read.events:
                chats.record_events(platform, read)
                changed += 1
            chats.mark_read(platform, read)
        except SQLAlchemyError:
            logger.exception("%s chat %s not saved", platform.value, read.channel_id)
    return changed


def sync(
    source: ChatSource, skip: Collection[str] = (), *, thorough: bool = True
) -> None:
    """One round of a platform, of every model but the ones in `skip`. The round
    raises if anything failed, so it counts as failed."""
    platform = source.platform
    started = time.monotonic()
    lookback = timedelta(seconds=settings.chatting.lookback_s)
    oldest = datetime.now(UTC) - lookback
    scope = ReadScope(
        seen=chats.last_read(platform),
        oldest=oldest,
        open_channels=chats.open_channels(platform),
        thorough=thorough,
    )
    reached = source.models()
    cover_models(source, reached.found)
    failed = reached.failed
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
    """Those of `usernames` (as the platform names them) read again now, what
    changed only; one that cannot be read is left out (logged) and does not take
    the others with it."""
    platform = source.platform
    oldest = datetime.now(UTC) - timedelta(seconds=settings.chatting.lookback_s)
    scope = ReadScope(
        seen=chats.last_read(platform),
        oldest=oldest,
        open_channels=chats.open_channels(platform),
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
    """Those delivering, with no fan past the limit: nothing a read would change."""
    late = time.time() - settings.chatting.unanswered_after_s
    waiting = {
        wait.model_username
        for wait in chats.open_waits(before=late)
        if wait.platform == platform
    }
    return models_delivering(platform) - waiting


def _run_platform(source: ChatSource) -> None:
    """Every model is read once per `webhook_sync_interval_s`; in between, all but
    those resting. A failed full round is not remembered as done, so it is tried again
    next minute."""
    platform = source.platform
    # First: what is stored already says it, whatever the read below does.
    note_bulk_sends(source)
    if get_text(mass_pending_key(platform)):
        # Done forgotten first: a failed round is then tried again next minute.
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
    """A platform's sweep, asked every minute. A failed round raises, so the sweep
    logs it."""
    source = source_for(platform)
    if source is not None:
        _run_platform(source)
