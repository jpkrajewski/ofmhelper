"""The "Chats unanswered" message, to the senders (Telegram), not in the daily
report: every `OFM_CHATTING_WAIT_EVENTS_CHECK_S` (10 min) a sweep lists every model
with fans waiting past the pop-up's own rule (`OFM_CHATTING_UNANSWERED_AFTER_S`,
5 min), and nothing when nobody waits. And 5 min after each fan message
(`rechecks.py`, webhook or sync alike) the same list goes out at once if that fan
still waits and was not told yet -- a look alone told fans 17 min late (prod
2026-11-06):

    Chatting:

    Stacked:

    Chats unanswered (2): @danewalkerjohnson
    - mastiffshrew (unanswered 2h 15m)
    - fan name (unanswered 12m)

Each list deletes the one before it in the chat (`Event.replaces`): the chat shows
only the newest; when nobody waits nothing is sent, so the last one stays.

Synced first, then checked: those models' chats are read from their platform again
(`sync.sync`, what changed only), and only fans still waiting are listed. A
platform this server does not read (no login here) is left out: what it stored can
be old. `OFM_CHATTING_EVENTS_PLATFORMS_OFF` (Fanvue for now) leaves platforms
out on purpose. The site's pop-up (`alerts.py`, `chats-alert.js`) is its own and
unchanged."""

from __future__ import annotations

import hashlib
import time
from typing import NamedTuple

from ofmhelpers.config import settings
from ofmhelpers.db.stores import chats
from ofmhelpers.errors import AppError
from ofmhelpers.features.chatting import rechecks
from ofmhelpers.features.chatting.sources import source_for
from ofmhelpers.features.chatting.sync import read_now
from ofmhelpers.features.events import Event, emit, listening
from ofmhelpers.log import get_logger
from ofmhelpers.schemas.chatting import ChatPlatform, OpenWait

logger = get_logger(__name__)

_NO_NAME = "(no name)"
# Fans named per model, the longest waiting first; the rest as "and N more".
_MAX_FANS = 10
_MINUTE = 60
_HOUR = 3600
_DAY = 24 * _HOUR

_Model = tuple[ChatPlatform, str]


def waited(seconds: float) -> str:
    """12m, 2h 15m, 3d 4h."""
    seconds = max(int(seconds), 0)
    if seconds < _HOUR:
        return f"{seconds // _MINUTE}m"
    if seconds < _DAY:
        return f"{seconds // _HOUR}h {seconds % _HOUR // _MINUTE}m"
    return f"{seconds // _DAY}d {seconds % _DAY // _HOUR}h"


def _waiting(now: float) -> dict[_Model, list[OpenWait]]:
    """Every model with fans waiting past the limit, platforms switched off left out."""
    off = settings.chatting.events_off()
    before = now - settings.chatting.unanswered_after_s
    found: dict[_Model, list[OpenWait]] = {}
    for wait in chats.open_waits(before=before):
        if wait.platform.value not in off:
            found.setdefault((wait.platform, wait.model_username), []).append(wait)
    return found


def _fresh(platform: ChatPlatform, usernames: set[str]) -> set[str]:
    """Of `usernames`, those this server reads, their chats read again now; none
    when the platform is not read here or the read failed (tried next look)."""
    source = source_for(platform)
    if source is None or not source.configured():
        return set()
    try:
        return read_now(source, usernames)
    except AppError:
        logger.warning("%s: not read before telling", platform.value, exc_info=True)
        return set()


def _block(
    platform: ChatPlatform, models: dict[_Model, list[OpenWait]], now: float
) -> str:
    lines = [f"{platform.label}:"]
    for (_, username), waits in sorted(models.items()):
        lines += ["", f"Chats unanswered ({len(waits)}): @{username}"]
        longest = sorted(waits, key=lambda w: w.started_at)
        lines += [
            f"- {wait.fan_name or _NO_NAME} (unanswered {waited(now - wait.started_at)})"
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
    waiting: dict[_Model, list[OpenWait]], chats_due: set[tuple[str, str]]
) -> list[OpenWait]:
    """The waits of the chats in `chats_due` nobody was told about yet."""
    due = [
        wait
        for waits in waiting.values()
        for wait in waits
        if (wait.platform.value, wait.channel_id) in chats_due
    ]
    return rechecks.untold(due)


class _Told(NamedTuple):
    """How many models a list named, and the chats due that could not be read."""

    models: int = 0
    unread: frozenset[tuple[str, str]] = frozenset()


def _tell(now: float, key: str | None, chats_due: set[tuple[str, str]] | None) -> _Told:
    """The list of every model with fans still waiting after a fresh read, one list
    at a time (`rechecks.one_teller`). `chats_due` (a recheck): only when one of
    those chats still waits untold, its key then named after those waits; else (a
    look) `key`."""
    if not listening():
        return _Told()
    with rechecks.one_teller():
        waiting = _waiting(now)
        due = _untold(waiting, chats_due) if chats_due is not None else []
        if not waiting or (chats_due is not None and not due):
            return _Told()
        fresh: set[_Model] = set()
        for platform in {platform for platform, _ in waiting}:
            names = {username for p, username in waiting if p == platform}
            fresh |= {(platform, username) for username in _fresh(platform, names)}
        unread = frozenset(
            (w.platform.value, w.channel_id)
            for w in due
            if (w.platform, w.model_username) not in fresh
        )
        still = {m: waits for m, waits in _waiting(now).items() if m in fresh}
        if chats_due is not None:
            new = _untold(still, chats_due)
            if not new:
                return _Told(unread=unread)
            ids = "\n".join(sorted(wait.id for wait in new)).encode()
            key = f"chatting.unanswered:wait:{hashlib.sha256(ids).hexdigest()[:32]}"
        if not still or key is None:
            return _Told(unread=unread)
        emit(_event(still, now, key))
        rechecks.mark_told([wait for waits in still.values() for wait in waits])
        return _Told(models=len(still), unread=unread)


def emit_waiting(now: float | None = None) -> int:
    """The look: the list of every model with fans still waiting after a fresh read
    (nobody waiting: nothing sent); how many models it named."""
    now = time.time() if now is None else now
    # One message per look, however often the sweep is restarted within it.
    look = int(now // settings.chatting.wait_events_check_s)
    return _tell(now, f"chatting.unanswered:{look}", None).models


def recheck_due(now: float | None = None) -> int:
    """The rechecks due by now (`rechecks.plan`, 5 min after a fan message): the
    list at once when one of those fans still waits and was not told yet; how many
    models it named. A recheck whose chat could not be read, or a list that failed,
    is due again at the next poll (`rechecks.again`)."""
    now = time.time() if now is None else now
    due = rechecks.take_due(now)
    if not due:
        return 0
    try:
        told = _tell(now, None, {r.chat for r in due})
    except Exception:
        rechecks.again(due, now)
        raise
    rechecks.again([r for r in due if r.chat in told.unread], now)
    return told.models
