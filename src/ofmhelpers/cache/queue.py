"""
The job queues: first in, first out, on RQ. A route does
`JobQueue(QueueName.MEDIA).put(run_job, job_id, fn, kwargs)` (`enqueue(...)` for
the default queue) and returns at once; a worker process takes the job at the
front of the line and writes its status to Postgres (db/stores/jobs.py), which
the API reads back.

Which queues exist, how many worker processes each has and their priority is
configuration (`config/settings.py`: `QueueName`, `InfraSettings.queues`); one
process is one job at a time. A module moves its work onto a queue by calling its
`put`.

Logfire: every `put` logs the queue, the function and how many are ahead of it;
every run is the worker's `rq {func}` span with its queue and how long it waited
(worker.py); `rq_job_id` ties the two together. Never a job's arguments: they
carry API keys.

It lives beside `redis.py` because that is what it is built on -- the queues are
the durable half of what this app keeps in Redis, the caches are the throwaway
half.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Self, cast

import logfire
from pydantic import BaseModel
from rq import Queue, Retry, Worker
from rq.job import Job

from ofmhelpers.cache.redis import get_redis
from ofmhelpers.config import settings
from ofmhelpers.config.settings import JOB_QUEUES, QueueName
from ofmhelpers.log import get_logger

if TYPE_CHECKING:
    from collections.abc import Callable

logger = get_logger(__name__)


def get_queue(name: QueueName = QueueName.DEFAULT) -> Queue:
    """A Queue bound to the shared Redis connection, with the generous default
    job timeout our long-polling generation jobs need (see settings).

    is_async comes from config: prod hands jobs to the worker; the test suite
    forces synchronous execution so enqueue() runs the job inline, matching the
    old BackgroundTasks behavior TestClient relied on."""
    return Queue(
        name.value,
        connection=get_redis(),
        default_timeout=settings.infra.rq_job_timeout_s,
        is_async=settings.infra.rq_async,
    )


class QueueJob(BaseModel):
    """A job a worker took off a queue: what its span records."""

    rq_job_id: str
    func: str
    queue: QueueName
    waited_s: float
    waiting: int

    @classmethod
    def taken(cls, job: Job, queue: Queue) -> Self:
        """`func` is the description `JobQueue.put` gave it, and `enqueued_at`
        was set by enqueuing it (RQ types it optional)."""
        waited = datetime.now(UTC) - cast("datetime", job.enqueued_at)
        return cls(
            rq_job_id=job.id,
            func=cast("str", job.description),
            queue=QueueName(queue.name),
            waited_s=round(waited.total_seconds(), 1),
            waiting=queue.count,
        )


@dataclass(frozen=True)
class JobQueue:
    """One first-in first-out line of background work: `put` joins the back,
    the queue's worker processes take from the front."""

    name: QueueName

    def put(
        self,
        fn: Callable[..., Any],
        *args: Any,
        job_timeout: int | None = None,
        retry: Retry | None = None,
        failure_ttl: int | None = None,
        **kwargs: Any,
    ) -> Any:
        """`fn(*args, **kwargs)` to the back of the line. `job_timeout`: its time
        limit once started, when it may run longer than the default (a long
        /clone video, made in pieces); waiting in line does not count. `retry`:
        RQ tries it again after it raised (the worker pool runs RQ's scheduler,
        so the waits hold). `failure_ttl`: how long RQ keeps it once it failed
        for good (RQ's default: a year).

        In synchronous mode (settings.infra.rq_async is False -- the test suite)
        it is called directly, in-process: no RQ, no serialization, so tests can
        still monkeypatch a task fn with a lambda."""
        if not settings.infra.rq_async:
            return fn(*args, **kwargs)
        queue = get_queue(self.name)
        ahead = queue.count + queue.started_job_registry.count
        # RQ's default description -- what the worker logs for every job it
        # starts -- is the call with its arguments, and those carry the kie.ai
        # API key a job runs with. The function name is all the log needs.
        rq_job = queue.enqueue(
            fn,
            *args,
            description=f"{fn.__module__}.{fn.__qualname__}",
            job_timeout=job_timeout or settings.infra.rq_job_timeout_s,
            retry=retry,
            failure_ttl=failure_ttl,
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

    def line(self) -> dict[str, int]:
        """Who waits, first in line first: app job id (`run_job`'s first
        argument) -> how many jobs are ahead of it, the ones being run included.
        Empty when nothing waits or it cannot tell (the synchronous test mode,
        Redis down)."""
        if not settings.infra.rq_async:
            return {}
        try:
            queue = get_queue(self.name)
            running = queue.started_job_registry.count
            waiting = Job.fetch_many(queue.get_job_ids(), connection=get_redis())
            ids = [str(j.args[0]) for j in waiting if j is not None and j.args]
        except Exception:
            logger.warning("could not read queue %s", self.name, exc_info=True)
            return {}
        return {job_id: running + place for place, job_id in enumerate(ids)}


def active_job_ids() -> set[str] | None:
    """The app job ids (`run_job`'s first argument) queued on a job queue
    (`JOB_QUEUES`), being taken off one by a worker, or being run by a live
    worker; None when it cannot tell (the synchronous test mode, Redis down).
    A worker killed mid-job (a deploy) stops counting once its heartbeat key
    expires.

    Read in the order a job moves, so one queued or starting is never
    missed: each queue's list, then its intermediate list (a worker's
    dequeue BLMOVEs the job there; its work horse later sets `current_job`
    and takes the job off that list in one MULTI), then the workers. A job a
    worker died holding in the intermediate list counts until rq's own
    cleanup fails it (about a minute)."""
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


def enqueue(fn, *args, job_timeout: int | None = None, **kwargs):
    """`put` on the default queue."""
    return JobQueue(QueueName.DEFAULT).put(fn, *args, job_timeout=job_timeout, **kwargs)


def enqueue_to(queue: QueueName, fn, *args, job_timeout: int | None = None, **kwargs):
    """`put` on `queue`."""
    return JobQueue(queue).put(fn, *args, job_timeout=job_timeout, **kwargs)
