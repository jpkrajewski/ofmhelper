# Copyright (C) 2026 jpkrajewski

import hashlib
import time

from ofmhelpers.config import settings
from ofmhelpers.db.chats import store as chats
from ofmhelpers.db.chats.schema import DeliveryKind
from ofmhelpers.db.report_runs import store as report_runs
from ofmhelpers.features.events import Event, emit
from ofmhelpers.log import get_logger
lazy from ofmhelpers.features.reports.daily import NAME as DAILY_NAME

logger = get_logger(__name__)

SLOT = "chatting.mass"
_DAY = 86400


def senders(since: float, until: float) -> list[str]:
    off = settings.chatting.events_off()
    return sorted(
        {
            t.model_username
            for t in chats.counts_between(DeliveryKind.MASS, since, until)
            if t.platform.value not in off
        },
    )


def lines(since: float, until: float) -> list[str]:
    found = senders(since, until)
    if not found:
        return ["Mass message sent: none"]
    return [f"Mass message sent: @{name}" for name in found]


def tell_mass(now: float | None = None) -> None:
    try:
        _tell(time.time() if now is None else now)
    except Exception:
        logger.exception("mass message list not told")


def _tell(now: float) -> None:
    last = report_runs.last_run(DAILY_NAME)
    since = last.ran_at if last is not None else now - _DAY
    if not senders(since, now):
        return
    found = lines(since, now)
    window = last.ran_at if last is not None else "none"
    digest = hashlib.sha256(f"{window}|{'|'.join(found)}".encode()).hexdigest()[:32]
    emit(
        Event(
            name="chatting.mass",
            title="Chatting:",
            text="\n".join(found),
            key=f"chatting.mass:{digest}",
            replaces=SLOT,
            at=now,
        ),
    )
