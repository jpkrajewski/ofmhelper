# Copyright (C) 2026 jpkrajewski

import hashlib
import logging
import time

from pydantic import BaseModel

from ofmhelpers.config import settings
from ofmhelpers.db.chats import store as chats
from ofmhelpers.db.chats.schema import ChatPlatform, OpenWait
from ofmhelpers.features.chatting import rechecks
from ofmhelpers.features.chatting.rechecks import ChatKey
from ofmhelpers.features.chatting.sources import source_for
from ofmhelpers.features.chatting.sync import read_now
from ofmhelpers.features.events import Event, emit, listening
from ofmhelpers.features.notifications import service as notifications
from ofmhelpers.integrations.chat_source import ChatSourceError

logger = logging.getLogger(__name__)

_NO_NAME = "(no name)"
_MAX_FANS = 10
_MINUTE = 60
_HOUR = 3600
_DAY = 24 * _HOUR

_Model = tuple[ChatPlatform, str]


def waited(seconds: float) -> str:
    seconds = max(int(seconds), 0)
    if seconds < _HOUR:
        return f"{seconds // _MINUTE}m"
    if seconds < _DAY:
        return f"{seconds // _HOUR}h {seconds % _HOUR // _MINUTE}m"
    return f"{seconds // _DAY}d {seconds % _DAY // _HOUR}h"


class _Waiting(BaseModel, frozen=True):
    by_model: dict[_Model, list[OpenWait]]


def _waiting(now: float) -> _Waiting:
    off = settings.chatting.events_off()
    before = now - settings.chatting.unanswered_after_s
    found: dict[_Model, list[OpenWait]] = {}
    for wait in chats.open_waits(before=before):
        if wait.platform.value not in off:
            found.setdefault((wait.platform, wait.model_username), []).append(wait)
    return _Waiting(by_model=found)


def _fresh(platform: ChatPlatform, usernames: set[str]) -> set[str]:
    source = source_for(platform)
    if source is None or not source.configured():
        return set()
    try:
        return read_now(source, usernames)
    except ChatSourceError:
        logger.warning("%s: not read before telling", platform.value, exc_info=True)
        return set()


def _block(
    platform: ChatPlatform,
    models: dict[_Model, list[OpenWait]],
    now: float,
) -> str:
    lines = [f"{platform.label}:"]
    for (_, username), waits in sorted(models.items()):
        lines += ["", f"Chats unanswered ({len(waits)}): @{username}"]
        longest = sorted(waits, key=lambda w: w.started_at)
        lines += [
            f"- {wait.fan_name or _NO_NAME} (unanswered "
            f"{waited(now - wait.started_at)})"
            for wait in longest[:_MAX_FANS]
        ]
        if len(longest) > _MAX_FANS:
            lines.append(f"- and {len(longest) - _MAX_FANS} more")
    return "\n".join(lines)


def _event(waiting: dict[_Model, list[OpenWait]], now: float, key: str) -> Event:
    blocks = [
        _block(platform, {m: w for m, w in waiting.items() if m[0] == platform}, now)
        for platform in ChatPlatform
        if any(model[0] == platform for model in waiting)
    ]
    return Event(
        name="chatting.unanswered",
        title="Chatting:",
        text="\n\n".join(blocks),
        key=key,
        replaces="chatting.unanswered",
    )


def _untold(
    waiting: dict[_Model, list[OpenWait]],
    chats_due: set[ChatKey],
) -> list[OpenWait]:
    due = [
        wait
        for waits in waiting.values()
        for wait in waits
        if ChatKey(platform=wait.platform.value, channel_id=wait.channel_id)
        in chats_due
    ]
    return rechecks.untold(due)


class _Told(BaseModel, frozen=True):
    models: int = 0
    unread: frozenset[ChatKey] = frozenset()


def _tell(now: float, key: str | None, chats_due: set[ChatKey] | None) -> _Told:
    if not listening():
        return _Told()
    with rechecks.one_teller() as mine:
        if not mine:
            return _Told(unread=frozenset(chats_due or ()))
        waiting = _waiting(now).by_model
        due = _untold(waiting, chats_due) if chats_due is not None else []
        if not waiting or (chats_due is not None and not due):
            return _Told()
        fresh: set[_Model] = set()
        for platform in {platform for platform, _ in waiting}:
            names = {username for p, username in waiting if p == platform}
            fresh |= {(platform, username) for username in _fresh(platform, names)}
        unread = frozenset(
            ChatKey(platform=w.platform.value, channel_id=w.channel_id)
            for w in due
            if (w.platform, w.model_username) not in fresh
        )
        still = {m: waits for m, waits in _waiting(now).by_model.items() if m in fresh}
        if chats_due is not None:
            new = _untold(still, chats_due)
            if not new:
                return _Told(unread=unread)
            ids = "\n".join(sorted(wait.id for wait in new)).encode()
            key = f"chatting.unanswered:wait:{hashlib.sha256(ids).hexdigest()[:32]}"
        if not still or key is None:
            return _Told(unread=unread)
        emit(_event(still, now, key))
        rechecks.mark_told([*waits for waits in still.values()])
        return _Told(models=len(still), unread=unread)


def emit_waiting(now: float | None = None) -> int:
    now = time.time() if now is None else now
    look = int(now // settings.chatting.wait_events_check_s)
    notifications.changed()
    return _tell(now, f"chatting.unanswered:{look}", None).models


def recheck_due(now: float | None = None) -> int:
    now = time.time() if now is None else now
    due = rechecks.take_due(now)
    if not due:
        return 0
    notifications.changed()
    try:
        told = _tell(now, None, {r.chat for r in due})
    except Exception:
        rechecks.again(due, now)
        raise
    rechecks.again([r for r in due if r.chat in told.unread], now)
    return told.models
