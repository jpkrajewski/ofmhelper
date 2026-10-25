"""
Integration test for the RQ queue + worker (cache/queue.py + run_job): enqueue a
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
from rq import SimpleWorker, Worker

from ofmhelpers.cache import (
    CLONE_LINKS_QUEUE,
    QUEUE_NAME,
    active_job_ids,
    enqueue,
    enqueue_to,
    get_queue,
    get_redis,
)
from ofmhelpers.web import recovery
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
    assert get_job(job_id).status == "running"

    _drain_queue()

    job = get_job(job_id)
    assert job.status == "done"
    assert job.result == [{"name": "out.mp4", "path": None}]


def test_worker_marks_a_raising_job_failed_never_stuck_running():
    job_id = create_job("seedance", {})
    get_queue().enqueue(run_job, job_id, worker_tasks.always_fails, {})

    _drain_queue()

    job = get_job(job_id)
    assert job.status == "failed"
    assert job.error == "Wrong API Key"


def test_worker_log_description_never_carries_the_job_arguments():
    # RQ logs the description of every job it starts; the kwargs hold the
    # kie.ai API key.
    job_id = create_job("seedance", {})
    rq_job = enqueue(
        run_job, job_id, worker_tasks.make_result, {"name": "secret-api-key"}
    )

    assert rq_job.description == "ofmhelpers.web.stores.jobs.run_job"
    _drain_queue()
    assert get_job(job_id).status == "done"


# --- what recovery sees as live (cache.queue.active_job_ids) -----------------


def _queue_run(queue: str) -> tuple[str, str]:
    """A job queued on `queue` the way a router does: (app id, rq id)."""
    job_id = create_job("clone_links", {})
    rq_job = enqueue_to(
        queue, run_job, job_id, worker_tasks.make_result, {"name": "out.mp4"}
    )
    return job_id, rq_job.id


def test_a_clone_links_run_waits_and_runs_on_its_own_queue():
    job_id, _ = _queue_run(CLONE_LINKS_QUEUE)

    assert get_queue().get_job_ids() == []
    assert active_job_ids() == {job_id}

    SimpleWorker([get_queue(CLONE_LINKS_QUEUE)], connection=get_redis()).work(
        burst=True
    )
    assert get_job(job_id).status == "done"


def test_a_job_a_worker_is_taking_off_the_queue_still_counts(monkeypatch):
    """Review #57: rq's dequeue BLMOVEs the job into `<queue>:intermediate`
    and only the work horse sets `current_job` later. In between it is in
    neither the queue nor a worker, and a sweep failed it as cut off."""
    monkeypatch.setenv("OFM_RECOVERY_ORPHAN_GRACE_S", "0")
    default_id, _ = _queue_run(QUEUE_NAME)
    links_id, _ = _queue_run(CLONE_LINKS_QUEUE)
    for name in (QUEUE_NAME, CLONE_LINKS_QUEUE):
        queue = get_queue(name)
        get_redis().lmove(queue.key, queue.intermediate_queue_key)  # the dequeue
        assert queue.get_job_ids() == []

    assert active_job_ids() == {default_id, links_id}
    assert recovery.fail_cut_off(active_job_ids(), set()) == {}
    assert get_job(default_id).status == "running"
    assert get_job(links_id).status == "running"


def test_a_job_a_live_worker_runs_counts():
    job_id, rq_id = _queue_run(CLONE_LINKS_QUEUE)
    queue = get_queue(CLONE_LINKS_QUEUE)
    get_redis().lrem(queue.key, 1, rq_id)  # taken off every list
    worker = Worker([queue], connection=get_redis())
    worker.register_birth()
    worker.set_current_job_id(rq_id)

    assert active_job_ids() == {job_id}

    worker.register_death()
    assert active_job_ids() == set()
