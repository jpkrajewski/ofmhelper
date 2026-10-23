"""Which provider request a job is waiting on, kept in Redis.

Written the moment a request is accepted and removed once its result is
saved (or it failed for good). While it is here:

- a re-run of the job (recovery, a worker restart) picks the same request
  back up instead of paying for a second generation;
- `web/recovery.py` can finish a job whose poll gave up, with the key the
  request was made on -- a key pasted into a form is in no env var.

The key is why this is Redis with a TTL and not the job row: a job's params
are shown on its page and must never carry it.

A job may make several requests, one per `part` (a long /clone video is
generated in pieces, 2026-10-23): each part is remembered on its own key, so
a re-run resumes every piece already paid for; `forget` drops them all.
"""

import time

from pydantic import BaseModel, Field
from redis.exceptions import RedisError

from ofmhelpers.cache import get_redis
from ofmhelpers.config import settings
from ofmhelpers.log import get_logger

logger = get_logger(__name__)

_PREFIX = "aigen:task:"


class ProviderTask(BaseModel):
    job_id: str
    # Which of the job's requests: 0 for a job that makes one.
    part: int = 0
    provider: str
    model: str
    task_id: str
    api_key: str
    created_at: float = Field(default_factory=time.time)

    @property
    def age_s(self) -> float:
        return time.time() - self.created_at


def _key(job_id: str, part: int = 0) -> str:
    """Part 0 keeps the key every job had before parts."""
    return f"{_PREFIX}{job_id}" if part == 0 else f"{_PREFIX}{job_id}:{part}"


def remember(task: ProviderTask) -> None:
    """A failed write is logged, not raised: the generation is already paid
    for, and losing only its resumability beats failing it."""
    try:
        get_redis().set(
            _key(task.job_id, task.part),
            task.model_dump_json(),
            ex=settings.aigen.resume_max_age_s,
        )
    except RedisError:
        logger.warning("could not remember task %s", task.task_id, exc_info=True)


def recall(job_id: str, part: int = 0) -> ProviderTask | None:
    try:
        raw = get_redis().get(_key(job_id, part))
    except RedisError:
        logger.warning("could not recall the task of job %s", job_id, exc_info=True)
        return None
    return ProviderTask.model_validate_json(raw) if raw else None


def forget(job_id: str) -> None:
    """Every part of the job."""
    try:
        redis = get_redis()
        redis.delete(_key(job_id), *redis.scan_iter(f"{_PREFIX}{job_id}:*"))
    except RedisError:
        logger.warning("could not forget the task of job %s", job_id, exc_info=True)


def remembered() -> list[ProviderTask]:
    """Every task still remembered: what recovery sweeps."""
    redis = get_redis()
    tasks = []
    for key in redis.scan_iter(f"{_PREFIX}*"):
        raw = redis.get(key)
        if raw:
            tasks.append(ProviderTask.model_validate_json(raw))
    return tasks
