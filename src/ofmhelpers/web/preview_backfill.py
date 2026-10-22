"""Gallery previews for jobs that finished before previews existed (or while
the previews worker was down): one pass at startup queues every finished job
whose previews are missing, newest first, so the cards people look at first
are ready first. See web/previews.py.

Idempotent -- a job with all its previews is skipped and every file is
written atomically -- so a pass after every deploy is safe; the Redis guard
only keeps the API's processes starting together from each queueing the same
jobs. Read-only on the history: it lists jobs unhealed
(`list_finished_jobs`), so a job whose files are missing is skipped, never
deleted.
"""

import asyncio

from redis.exceptions import RedisError

from ofmhelpers.cache import PREVIEWS_QUEUE, enqueue_to, get_redis
from ofmhelpers.config import settings
from ofmhelpers.log import get_logger
from ofmhelpers.web.previews import is_warm, warm_previews
from ofmhelpers.web.stores.jobs import list_finished_jobs

logger = get_logger(__name__)

GUARD_KEY = "previews:backfill"


def backfill() -> int:
    """Queue every finished job missing a preview; how many were queued
    (0 when another process holds the guard)."""
    if not get_redis().set(
        GUARD_KEY, "1", nx=True, ex=settings.web.preview_backfill_guard_s
    ):
        return 0
    queued = 0
    for job in list_finished_jobs():
        sources = job.local_paths()
        if is_warm(job.id, sources):
            continue
        enqueue_to(PREVIEWS_QUEUE, warm_previews, job.id, sources)
        queued += 1
    logger.info("preview backfill: %d jobs queued", queued)
    return queued


async def preview_backfill_once() -> None:
    try:
        await asyncio.to_thread(backfill)
    except RedisError:
        logger.warning("preview backfill skipped: Redis unavailable", exc_info=True)
