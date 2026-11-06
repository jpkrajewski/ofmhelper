"""The "Chats unanswered" message, to the senders (Telegram), not in the daily
report: every `OFM_CHATTING_WAIT_EVENTS_CHECK_S` (15 min) a sweep lists every model
with fans waiting past the pop-up's own rule (`OFM_CHATTING_UNANSWERED_AFTER_S`,
5 min), and nothing when nobody waits:

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

import time

from ofmhelpers.config import settings
from ofmhelpers.db.stores import chats
from ofmhelpers.errors import AppError
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


def _event(waiting: dict[_Model, list[OpenWait]], now: float) -> Event:
    blocks = [
        _block(platform, {m: w for m, w in waiting.items() if m[0] == platform}, now)
        for platform in ChatPlatform
        if any(model[0] == platform for model in waiting)
    ]
    # One message per look, however often the sweep is restarted within it.
    look = int(now // settings.chatting.wait_events_check_s)
    return Event(
        name="chatting.unanswered",
        title="Chatting:",
        text="\n\n".join(blocks),
        key=f"chatting.unanswered:{look}",
        replaces="chatting.unanswered",
    )


def emit_waiting(now: float | None = None) -> int:
    """The list of every model with fans still waiting after a fresh read (nobody
    waiting: nothing sent); how many models it named."""
    now = time.time() if now is None else now
    if not listening():
        return 0
    waiting = _waiting(now)
    if not waiting:
        return 0
    fresh: set[_Model] = set()
    for platform in {platform for platform, _ in waiting}:
        names = {username for p, username in waiting if p == platform}
        fresh |= {(platform, username) for username in _fresh(platform, names)}
    still = {model: waits for model, waits in _waiting(now).items() if model in fresh}
    if still:
        emit(_event(still, now))
    return len(still)
