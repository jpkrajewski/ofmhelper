"""
RQ queue the API enqueues background work onto, and the worker container
(`rq worker-pool`) consumes. This replaces FastAPI BackgroundTasks: instead of
running a job in-process, a route does `enqueue(run_job, job_id, fn, kwargs)`
and returns immediately (logged by function name only -- the kwargs carry
API keys); a separate worker process runs it, writing status to
Postgres (see web/stores/jobs.py) that the API can then read back.

It lives beside `redis.py` because that is what it is built on -- the queue is
the durable half of what this app keeps in Redis, the caches are the
throwaway half.
"""

from __future__ import annotations

from rq import Queue, Worker
from rq.job import Job

from ofmhelpers.cache.redis import get_redis
from ofmhelpers.config import settings
from ofmhelpers.log import get_logger

logger = get_logger(__name__)

# The queue name both the API (enqueue) and the worker (rq worker-pool, which
# defaults to this queue) agree on.
QUEUE_NAME = "default"
# Gallery previews (web/previews.py): ffmpeg encodes, consumed by their own
# low-priority worker so they never hold a generation worker.
PREVIEWS_QUEUE = "previews"
# /clone-links runs (web/clone_links/run.py): a 30-120 link Generate on its
# own worker pool (compose `clone-links-worker`), so it never holds the
# default queue for hours.
CLONE_LINKS_QUEUE = "clone-links"
# Every queue `run_job` is put on: what `active_job_ids` reads.
JOB_QUEUES = (QUEUE_NAME, CLONE_LINKS_QUEUE)


def get_queue(name: str = QUEUE_NAME) -> Queue:
    """A Queue bound to the shared Redis connection, with the generous default
    job timeout our long-polling generation jobs need (see settings).

    is_async comes from config: prod hands jobs to the worker; the test suite
    forces synchronous execution so enqueue() runs the job inline, matching the
    old BackgroundTasks behavior TestClient relied on."""
    return Queue(
        name,
        connection=get_redis(),
        default_timeout=settings.infra.rq_job_timeout_s,
        is_async=settings.infra.rq_async,
    )


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
    """`enqueue_to` the default queue (see below)."""
    return enqueue_to(QUEUE_NAME, fn, *args, job_timeout=job_timeout, **kwargs)


def enqueue_to(queue: str, fn, *args, job_timeout: int | None = None, **kwargs):
    """Swap `background_tasks.add_task(fn, ...)` for `enqueue(fn, ...)` with the
    least possible delta.

    In synchronous mode (settings.infra.rq_async is False -- the test suite)
    the function is called directly, in-process, exactly like the old
    BackgroundTasks: no RQ, no serialization, so tests can still monkeypatch a
    task fn with a lambda. In async mode (prod) it goes onto the queue for the
    worker. `job_timeout`: this job's time limit, when it may run longer than
    the queue's default (a long /clone video, made in pieces)."""
    if not settings.infra.rq_async:
        return fn(*args, **kwargs)
    # RQ's default description -- what the worker logs for every job it
    # starts -- is the call with its arguments, and those carry the kie.ai
    # API key a job runs with. The function name is all the log needs.
    return get_queue(queue).enqueue(
        fn,
        *args,
        description=f"{fn.__module__}.{fn.__qualname__}",
        job_timeout=job_timeout or settings.infra.rq_job_timeout_s,
        **kwargs,
    )
