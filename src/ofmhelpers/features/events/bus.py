# Copyright (C) 2026 jpkrajewski

import logging

from ofmhelpers.cache import JobQueue, QueueName
from ofmhelpers.config import settings
from ofmhelpers.features.events.dispatch import dispatch
from ofmhelpers.integrations.senders.schema import Event

logger = logging.getLogger(__name__)


def _absolute(event: Event) -> Event:
    base = settings.web.base_url.rstrip("/")
    if event.url and event.url.startswith("/") and base:
        return event.model_copy(update={"url": f"{base}{event.url}"})
    return event


def emit(event: Event) -> None:
    try:
        if not isinstance(event, Event):
            msg = f"emit takes an Event, not {type(event).__name__}"
            raise TypeError(msg)  # noqa: TRY301
        JobQueue(name=QueueName.EVENTS).put(
            dispatch,
            (_absolute(event).model_dump(mode="json"),),
        )
    except Exception:
        logger.warning(
            "event %s not queued",
            event.name if isinstance(event, Event) else type(event).__name__,
            exc_info=True,
        )
