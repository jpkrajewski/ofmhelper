"""The "Mass message sent" message to the senders (Telegram): every model that
sent a mass message since the last daily report, one line each, no count (owner
2026-11-07: it is just the news that one went out).

    Chatting:

    Mass message sent: @danewalkerjohnson
    Mass message sent: @walker_dane

Sent again whenever a mass message is stored (`features/chatting/webhooks.py`:
a platform's own event, or a bulk send seen by its size), each list taking the
place of the one before it (`Event.replaces`, like "Chats unanswered"); the daily
report's Chatting part is the same message (`daily.py`), so the chat shows only
the newest. The same list twice goes out once (its key). Platforms in
`OFM_CHATTING_EVENTS_PLATFORMS_OFF` (Fanvue for now) are left out."""

from __future__ import annotations

import hashlib
import time

from ofmhelpers.config import settings
from ofmhelpers.db.stores import chats, report_runs
from ofmhelpers.features.events import Event, emit
from ofmhelpers.log import get_logger
from ofmhelpers.schemas.chatting import DeliveryKind

logger = get_logger(__name__)

# The message every list takes the place of.
SLOT = "chatting.mass"
_DAY = 86400


def senders(since: float, until: float) -> list[str]:
    """The models that sent a mass message in [since, until), by name."""
    off = settings.chatting.events_off()
    return sorted(
        {
            t.model_username
            for t in chats.counts_between(DeliveryKind.MASS, since, until)
            if t.platform.value not in off
        }
    )


def lines(since: float, until: float) -> list[str]:
    found = senders(since, until)
    if not found:
        return ["Mass message sent: none"]
    return [f"Mass message sent: @{name}" for name in found]


def tell_mass(now: float | None = None) -> None:
    """The list since the last daily report (a day back before the first), at
    once: nothing when it names nobody (only a platform left out sent). Never
    raises: a mass message is stored whatever becomes of this news (logged)."""
    try:
        # Taken before anything is read (see `Event.at` in `_tell`).
        _tell(time.time() if now is None else now)
    except Exception:
        logger.exception("mass message list not told")


def _tell(now: float) -> None:
    # Lazily: daily.py builds its Chatting part from `lines`.
    from ofmhelpers.features.reports.daily import NAME

    last = report_runs.last_run(NAME)
    since = last.ran_at if last is not None else now - _DAY
    if not senders(since, now):
        return
    found = lines(since, now)
    # Per report window (not the moving day before the first report): the same
    # list in one window is one message.
    window = last.ran_at if last is not None else "none"
    digest = hashlib.sha256(f"{window}|{'|'.join(found)}".encode()).hexdigest()[:32]
    emit(
        Event(
            name="chatting.mass",
            title="Chatting:",
            text="\n".join(found),
            key=f"chatting.mass:{digest}",
            replaces=SLOT,
            # When the list was read, not built: of two lists told at once, the
            # one read later (it names more) is the one the chat keeps.
            at=now,
        )
    )
