# Copyright (C) 2026 jpkrajewski

import logging
import time

from pydantic import BaseModel, Field
from redis.exceptions import RedisError

from ofmhelpers.cache import get_redis
from ofmhelpers.config import settings

logger = logging.getLogger(__name__)

_PREFIX = "aigen:task:"


class ProviderTask(BaseModel):
    job_id: str
    part: int = 0
    provider: str
    model: str
    task_id: str
    api_key: str
    created_at: float = Field(default_factory=time.time)
    state: str | None = None
    cost_usd: float | None = None
    seconds: float | None = None

    @property
    def age_s(self) -> float:
        return time.time() - self.created_at


def _key(job_id: str, part: int = 0) -> str:
    return f"{_PREFIX}{job_id}" if part == 0 else f"{_PREFIX}{job_id}:{part}"


def remember(task: ProviderTask) -> None:
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


def settle(job_id: str, part: int, state: str, cost_usd: float | None) -> None:
    task = recall(job_id, part)
    if task is None:
        return
    remember(
        task.model_copy(
            update={
                "state": state,
                "cost_usd": cost_usd,
                "seconds": round(task.age_s, 1),
            },
        ),
    )


def of_job(job_id: str) -> list[ProviderTask]:
    try:
        redis = get_redis()
        keys = [_key(job_id), *redis.scan_iter(f"{_PREFIX}{job_id}:*")]
        found = [
            ProviderTask.model_validate_json(raw) for raw in redis.mget(keys) if raw
        ]
    except RedisError:
        logger.warning("could not read the tasks of job %s", job_id, exc_info=True)
        return []
    return sorted(found, key=lambda task: task.part)


_SENT_PREFIX = "aigen:sent:"


def _sent_key(job_id: str, part: int) -> str:
    return f"{_SENT_PREFIX}{job_id}:{part}"


def forget_part(job_id: str, part: int) -> None:
    try:
        get_redis().delete(_key(job_id, part))
    except RedisError:
        logger.warning("could not forget the task of job %s", job_id, exc_info=True)


def forget(job_id: str) -> None:
    try:
        redis = get_redis()
        redis.delete(
            _key(job_id),
            *redis.scan_iter(f"{_PREFIX}{job_id}:*"),
            *redis.scan_iter(f"{_SENT_PREFIX}{job_id}:*"),
        )
    except RedisError:
        logger.warning("could not forget the task of job %s", job_id, exc_info=True)


def waiting(job_id: str) -> bool:
    try:
        return any(get_redis().scan_iter(f"{_SENT_PREFIX}{job_id}:*"))
    except RedisError:
        logger.warning("could not read the submits of job %s", job_id, exc_info=True)
        return True


def remember_sent(task: ProviderTask) -> None:
    try:
        get_redis().set(
            _sent_key(task.job_id, task.part),
            task.model_dump_json(),
            ex=settings.aigen.resume_max_age_s,
        )
    except RedisError:
        logger.warning(
            "could not note the submit of job %s",
            task.job_id,
            exc_info=True,
        )


def sent(job_id: str, part: int = 0) -> ProviderTask | None:
    try:
        raw = get_redis().get(_sent_key(job_id, part))
    except RedisError:
        logger.warning("could not read the submit of job %s", job_id, exc_info=True)
        return None
    return ProviderTask.model_validate_json(raw) if raw else None


def forget_sent(job_id: str, part: int = 0) -> None:
    try:
        get_redis().delete(_sent_key(job_id, part))
    except RedisError:
        logger.warning("could not drop the submit of job %s", job_id, exc_info=True)


def found(job_id: str, part: int, task_id: str) -> ProviderTask | None:
    noted = sent(job_id, part)
    if noted is None:
        return None
    task = noted.model_copy(update={"task_id": task_id, "created_at": time.time()})
    remember(task)
    forget_sent(job_id, part)
    return task


def remembered() -> list[ProviderTask]:
    redis = get_redis()
    tasks = []
    for key in redis.scan_iter(f"{_PREFIX}*"):
        raw = redis.get(key)
        if raw:
            tasks.append(ProviderTask.model_validate_json(raw))
    return tasks
