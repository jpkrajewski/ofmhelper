from ofmhelpers.cache import QueueName, enqueue_to, get_redis
from ofmhelpers.config import settings
from ofmhelpers.db.jobs.store import list_finished_jobs
from ofmhelpers.features.previews.render import is_warm, warm_previews
from ofmhelpers.log import get_logger

logger = get_logger(__name__)

GUARD_KEY = "previews:backfill"


def backfill() -> int:
    if not get_redis().set(
        GUARD_KEY, "1", nx=True, ex=settings.web.preview_backfill_guard_s
    ):
        return 0
    queued = 0
    for job in list_finished_jobs():
        sources = job.local_paths()
        if is_warm(job.id, sources):
            continue
        enqueue_to(QueueName.PREVIEWS, warm_previews, (job.id, sources))
        queued += 1
    logger.info("preview backfill: %d jobs queued", queued)
    return queued
