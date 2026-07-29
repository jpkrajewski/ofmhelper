"""
RQ queue the API enqueues background work onto, and the worker container
(`rq worker-pool`) consumes. This replaces FastAPI BackgroundTasks: instead of
running a job in-process, a route does `enqueue(run_job, job_id, fn, kwargs)`
and returns immediately; a separate worker process runs it, writing status to
Postgres (see web/stores/jobs.py) that the API can then read back.

Connection + queue are built lazily from settings.infra (never at import), for
the same reason the DB engine is: the worker and the API must bind to whatever
OFM_REDIS_URL is set at runtime, and tests point it at a test broker. Cached
per URL so production reuses one connection pool.
"""

from __future__ import annotations

from redis import Redis
from rq import Queue

from ofmhelpers.config import settings

# The queue name both the API (enqueue) and the worker (rq worker-pool, which
# defaults to this queue) agree on.
QUEUE_NAME = "default"

_redis: Redis | None = None
_redis_url: str | None = None


def get_redis() -> Redis:
    global _redis, _redis_url
    url = settings.infra.redis_url
    if _redis is None or url != _redis_url:
        _redis = Redis.from_url(url)
        _redis_url = url
    return _redis


def get_queue() -> Queue:
    """A Queue bound to the shared Redis connection, with the generous default
    job timeout our long-polling generation jobs need (see settings).

    is_async comes from config: prod hands jobs to the worker; the test suite
    forces synchronous execution so enqueue() runs the job inline, matching the
    old BackgroundTasks behavior TestClient relied on."""
    return Queue(
        QUEUE_NAME,
        connection=get_redis(),
        default_timeout=settings.infra.rq_job_timeout_s,
        is_async=settings.infra.rq_async,
    )


def enqueue(fn, *args, **kwargs):
    """Swap `background_tasks.add_task(fn, ...)` for `enqueue(fn, ...)` with the
    least possible delta.

    In synchronous mode (settings.infra.rq_async is False -- the test suite)
    the function is called directly, in-process, exactly like the old
    BackgroundTasks: no RQ, no serialization, so tests can still monkeypatch a
    task fn with a lambda. In async mode (prod) it goes onto the queue for the
    worker."""
    if not settings.infra.rq_async:
        return fn(*args, **kwargs)
    return get_queue().enqueue(fn, *args, **kwargs)
