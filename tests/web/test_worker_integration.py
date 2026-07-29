"""
Integration test for the RQ queue + worker (web/queue.py + run_job): enqueue a
job, run a real worker against the test Redis, and assert the job transitions
through Postgres exactly as the API would observe it -- the durability the old
in-process BackgroundTasks never had.

Requires the compose Redis (and Postgres) up:
    docker compose up -d postgres redis

Uses SimpleWorker in burst mode -- it runs each job in-process (no os.fork, so
it works on Windows too) and returns once the queue is drained.
"""

import pytest
import worker_tasks
from rq import SimpleWorker

from ofmhelpers.web.queue import get_queue, get_redis
from ofmhelpers.web.stores.jobs import create_job, get_job, run_job


@pytest.fixture(autouse=True)
def _async_broker(monkeypatch):
    # The suite runs jobs inline (OFM_RQ_ASYNC=false); this module needs the
    # real async path so a separate worker actually drains the queue.
    monkeypatch.setenv("OFM_RQ_ASYNC", "true")
    get_redis().flushdb()
    yield
    get_redis().flushdb()


def _drain_queue():
    worker = SimpleWorker([get_queue()], connection=get_redis())
    worker.work(burst=True)


def test_enqueued_job_runs_in_a_worker_and_finishes_in_postgres():
    job_id = create_job("seedance", {})
    get_queue().enqueue(run_job, job_id, worker_tasks.make_result, {"name": "out.mp4"})

    # Not run yet -- still queued, so Postgres still shows it running.
    assert get_job(job_id)["status"] == "running"

    _drain_queue()

    job = get_job(job_id)
    assert job["status"] == "done"
    assert job["result"] == [{"name": "out.mp4", "path": None}]


def test_worker_marks_a_raising_job_failed_never_stuck_running():
    job_id = create_job("seedance", {})
    get_queue().enqueue(run_job, job_id, worker_tasks.always_fails, {})

    _drain_queue()

    job = get_job(job_id)
    assert job["status"] == "failed"
    assert job["error"] == "Wrong API Key"
