# Copyright (C) 2026 jpkrajewski

import logging

from ofmhelpers.cache.queue import queue_loads
from ofmhelpers.monitoring.sentry import heartbeat

logger = logging.getLogger(__name__)


def report_alive() -> None:
    loads = queue_loads()
    unattended = [load.name for load in loads if load.workers == 0]
    if not loads or unattended:
        logger.error(
            "no worker takes jobs from: %s",
            ", ".join(unattended) or "any queue (Redis unreadable)",
        )
    heartbeat(healthy=bool(loads) and not unattended)
