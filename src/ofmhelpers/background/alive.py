# Copyright (C) 2026 jpkrajewski

from ofmhelpers.cache.queue import queue_loads
from ofmhelpers.log import get_logger
from ofmhelpers.monitoring.sentry import heartbeat

logger = get_logger(__name__)


def report_alive() -> None:
    loads = queue_loads()
    unattended = [load.name for load in loads if load.workers == 0]
    if not loads or unattended:
        logger.error(
            "no worker takes jobs from: %s",
            ", ".join(unattended) or "any queue (Redis unreadable)",
        )
    heartbeat(healthy=bool(loads) and not unattended)
