# Copyright (C) 2026 jpkrajewski

import logging
import time
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError

from ofmhelpers.cache.queue import QueueLoad, queue_loads
from ofmhelpers.cache.redis import used_bytes
from ofmhelpers.config import settings
from ofmhelpers.db.database.store import database_bytes
from ofmhelpers.db.jobs.store import running_jobs
from ofmhelpers.db.users.store import list_users
from ofmhelpers.features.server import host
from ofmhelpers.features.server.models import Host, RunningJob, ServerStatus

logger = logging.getLogger(__name__)


def duration(seconds: float) -> str:
    if seconds < 60:  # noqa: PLR2004
        return f"{seconds:.0f} s"
    if seconds < 3600:  # noqa: PLR2004
        return f"{seconds / 60:.0f} min"
    if seconds < 86400:  # noqa: PLR2004
        return f"{seconds / 3600:.1f} h"
    return f"{seconds / 86400:.0f} d"


def _database() -> int | None:
    try:
        return database_bytes()
    except SQLAlchemyError:
        logger.warning("could not read the database size", exc_info=True)
        return None


def _jobs() -> list[RunningJob]:
    who = {str(u.id): u.email for u in list_users()}
    now = time.time()
    jobs = sorted(running_jobs(), key=lambda j: j.created_at)
    return [
        RunningJob(
            task=j.task,
            who=who.get(j.actor or "", j.actor or "system"),
            running_s=now - j.created_at,
        )
        for j in jobs
    ]


def problems(box: Host, queues: list[QueueLoad], jobs: list[RunningJob]) -> list[str]:
    cfg = settings.server
    found: list[str] = []
    if box.cpu_percent >= cfg.warn_percent:
        found.append(f"CPU {box.cpu_percent:.0f}% busy")
    if box.load[0] > box.cpus:
        found.append(
            f"Load {box.load[0]:.1f} on {box.cpus} CPUs: work is waiting for CPU",
        )
    if box.memory.percent >= cfg.warn_percent:
        found.append(f"RAM {box.memory.percent:.0f}% used")
    if box.disk.percent >= cfg.warn_percent:
        found.append(f"Disk {box.disk.percent:.0f}% full")
    for q in queues:
        if q.waiting and not q.workers:
            found.append(f"Queue {q.name}: {q.waiting} waiting, no worker running")
        elif q.oldest_wait_s is not None and q.oldest_wait_s >= cfg.wait_warn_s:
            found.append(
                f"Queue {q.name}: {q.waiting} waiting, first one for "
                f"{duration(q.oldest_wait_s)}",
            )
    slow = [j for j in jobs if j.running_s >= cfg.run_warn_s]
    if slow:
        found.append(
            f"{len(slow)} job{'s' if len(slow) > 1 else ''} running over "
            f"{duration(cfg.run_warn_s)}",
        )
    return found


def server_status() -> ServerStatus:
    uploads = Path(settings.infra.uploads_root)
    box = host.host(uploads, settings.server.cpu_sample_s)
    queues = queue_loads()
    jobs = _jobs()
    return ServerStatus(
        host=box,
        folders=host.folders(uploads),
        database_bytes=_database(),
        redis_bytes=used_bytes(),
        queues=queues,
        jobs=jobs[: settings.server.jobs_shown],
        problems=problems(box, queues, jobs),
    )
