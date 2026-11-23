from datetime import UTC, datetime
from typing import Any, Self, cast
lazy from collections.abc import Callable

import logfire
from pydantic import BaseModel
from rq import Queue, Retry, Worker
from rq.job import Job, JobStatus, get_current_job

from ofmhelpers.cache.redis import get_redis
from ofmhelpers.config import settings
from ofmhelpers.config.settings import JOB_QUEUES, QueueName
from ofmhelpers.log import get_logger

logger = get_logger(__name__)


def get_queue(name: QueueName = QueueName.DEFAULT) -> Queue:
    return Queue(
        name.value,
        connection=get_redis(),
        default_timeout=settings.infra.rq_job_timeout_s,
        is_async=settings.infra.rq_async,
    )


class QueueJob(BaseModel):
    rq_job_id: str
    func: str
    queue: QueueName
    waited_s: float
    waiting: int

    @classmethod
    def taken(cls, job: Job, queue: Queue) -> Self:
        waited = datetime.now(UTC) - cast("datetime", job.enqueued_at)
        return cls(
            rq_job_id=job.id,
            func=cast("str", job.description),
            queue=QueueName(queue.name),
            waited_s=round(waited.total_seconds(), 1),
            waiting=queue.count,
        )


_IN_LINE = frozenset(
    {JobStatus.QUEUED, JobStatus.STARTED, JobStatus.DEFERRED, JobStatus.SCHEDULED}
)
_ONCE_FAILURE_TTL_S = 3600


class Line(BaseModel, frozen=True):
    running: int = 0
    waiting: list[str] = []

    def __contains__(self, job_id: object) -> bool:
        return job_id in self.waiting

    def ahead(self, job_id: str) -> int | None:
        places = [i for i, waiting in enumerate(self.waiting) if waiting == job_id]
        return self.running + places[-1] if places else None


class JobQueue(BaseModel, frozen=True):
    name: QueueName

    def put(
        self,
        fn: Callable[..., Any],
        args: tuple[Any, ...] = (),
        kwargs: dict[str, Any] | None = None,
        job_timeout: int | None = None,
        retry: Retry | None = None,
        failure_ttl: int | None = None,
        at_front: bool = False,
    ) -> Job | None:
        kwargs = kwargs or {}
        if not settings.infra.rq_async:
            fn(*args, **kwargs)
            return None
        queue = get_queue(self.name)
        ahead = queue.started_job_registry.count + (0 if at_front else queue.count)
        rq_job = queue.enqueue(
            fn,
            *args,
            description=f"{fn.__module__}.{fn.__qualname__}",
            job_timeout=job_timeout or settings.infra.rq_job_timeout_s,
            retry=retry,
            failure_ttl=failure_ttl,
            at_front=at_front,
            **kwargs,
        )
        logfire.info(
            "queue {queue}: {func} in line, {ahead} ahead",
            queue=self.name.value,
            func=fn.__qualname__,
            ahead=ahead,
            rq_job_id=rq_job.id,
        )
        return rq_job

    def put_once(
        self, key: str, fn: Callable[..., Any], args: tuple[Any, ...] = ()
    ) -> bool:
        if not settings.infra.rq_async:
            fn(*args)
            return True
        queue = get_queue(self.name)
        before = queue.fetch_job(key)
        if before is not None and before.get_status() in _IN_LINE:
            return False
        queue.enqueue(
            fn,
            *args,
            job_id=key,
            description=f"{fn.__module__}.{fn.__qualname__}",
            result_ttl=0,
            failure_ttl=_ONCE_FAILURE_TTL_S,
        )
        return True

    def drop(self, job_id: str) -> bool:
        if not settings.infra.rq_async:
            return False
        try:
            queue = get_queue(self.name)
            waiting = Job.fetch_many(queue.get_job_ids(), connection=get_redis())
            for rq_job in waiting:
                if rq_job is None or not rq_job.args or str(rq_job.args[0]) != job_id:
                    continue
                if not queue.remove(rq_job):
                    return False
                rq_job.delete(remove_from_queue=False)
                return True
        except Exception:
            logger.warning(
                "could not drop %s from %s", job_id, self.name, exc_info=True
            )
        return False

    def line(self) -> Line:
        if not settings.infra.rq_async:
            return Line()
        try:
            queue = get_queue(self.name)
            running = queue.started_job_registry.count
            waiting = Job.fetch_many(queue.get_job_ids(), connection=get_redis())
            ids = [str(j.args[0]) for j in waiting if j is not None and j.args]
        except Exception:
            logger.warning("could not read queue %s", self.name, exc_info=True)
            return Line()
        return Line(running=running, waiting=ids)


def active_job_ids() -> set[str] | None:
    if not settings.infra.rq_async:
        return None
    try:
        redis = get_redis()
        rq_ids: list[str] = []
        for name in JOB_QUEUES:
            queue = get_queue(name)
            rq_ids += queue.get_job_ids()
            rq_ids += queue.intermediate_queue.get_job_ids()
        for worker in Worker.all(connection=redis):
            current = worker.get_current_job_id()
            if current is not None:
                rq_ids.append(current)
        rq_jobs = Job.fetch_many(rq_ids, connection=redis)
        return {str(j.args[0]) for j in rq_jobs if j is not None and j.args}
    except Exception:
        logger.warning("could not read the queue's active jobs", exc_info=True)
        return None


class QueueLoad(BaseModel):
    name: str
    workers: int
    busy: int
    waiting: int
    failed: int
    oldest_wait_s: float | None


def _waited_s(queue: Queue) -> float | None:
    first = queue.get_job_ids(0, 1)
    job = Job.fetch_many(first, connection=get_redis())[0] if first else None
    if job is None or job.enqueued_at is None:
        return None
    at = job.enqueued_at
    at = at if at.tzinfo else at.replace(tzinfo=UTC)
    return (datetime.now(UTC) - at).total_seconds()


def queue_loads() -> list[QueueLoad]:
    try:
        workers = Worker.all(connection=get_redis())
        loads = []
        for name in QueueName:
            queue = get_queue(name)
            mine = [w for w in workers if name.value in w.queue_names()]
            loads.append(
                QueueLoad(
                    name=name.value,
                    workers=len(mine),
                    busy=sum(1 for w in mine if w.get_state() == "busy"),
                    waiting=queue.count,
                    failed=queue.failed_job_registry.count,
                    oldest_wait_s=_waited_s(queue),
                )
            )
    except Exception:
        logger.warning("could not read the queues", exc_info=True)
        return []
    return loads


class RunningJob(BaseModel, frozen=True):
    queue: QueueName
    timeout: int | None = None


def running_job() -> RunningJob | None:
    job = get_current_job()
    if job is None:
        return None
    timeout = None if job.timeout is None else int(job.timeout)
    return RunningJob(queue=QueueName(job.origin), timeout=timeout)


def enqueue(
    fn: Callable[..., Any],
    args: tuple[Any, ...] = (),
    kwargs: dict[str, Any] | None = None,
    job_timeout: int | None = None,
) -> Job | None:
    return JobQueue(name=QueueName.DEFAULT).put(
        fn, args, kwargs, job_timeout=job_timeout
    )


def enqueue_to(
    queue: QueueName,
    fn: Callable[..., Any],
    args: tuple[Any, ...] = (),
    kwargs: dict[str, Any] | None = None,
    job_timeout: int | None = None,
    at_front: bool = False,
) -> Job | None:
    return JobQueue(name=queue).put(
        fn, args, kwargs, job_timeout=job_timeout, at_front=at_front
    )
