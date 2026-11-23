# Copyright (C) 2026 jpkrajewski

import pytest
import worker_tasks
from rq import SimpleWorker, Worker
from rq.job import Job

from ofmhelpers.background import recovery
from ofmhelpers.cache import (
    QueueName,
    active_job_ids,
    enqueue,
    enqueue_to,
    get_queue,
    get_redis,
)
from ofmhelpers.db.jobs.store import create_job, get_job
from ofmhelpers.features.jobs.runner import run_job


@pytest.fixture(autouse=True)
def _async_broker(monkeypatch):
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

    assert get_job(job_id).status == "running"

    _drain_queue()

    job = get_job(job_id)
    assert job.status == "done"
    assert job.result == [{"name": "out.mp4", "path": None}]


def test_a_job_queued_under_run_jobs_old_path_still_runs():
    old_path = "ofmhelpers.db.stores.jobs.run_job"
    job_id = create_job("seedance", {})
    rq_job = get_queue().enqueue(
        old_path,
        job_id,
        worker_tasks.make_result,
        {"name": "out.mp4"},
    )
    assert rq_job.func_name == old_path

    _drain_queue()

    job = get_job(job_id)
    assert job.status == "done"
    assert job.result == [{"name": "out.mp4", "path": None}]


def test_a_failing_job_queued_under_the_old_path_is_recorded_failed():
    job_id = create_job("seedance", {})
    get_queue().enqueue(
        "ofmhelpers.db.stores.jobs.run_job",
        job_id,
        worker_tasks.always_fails,
        {},
    )

    _drain_queue()

    job = get_job(job_id)
    assert job.status == "failed"
    assert job.error == "Wrong API Key"


def test_worker_marks_a_raising_job_failed_never_stuck_running():
    job_id = create_job("seedance", {})
    get_queue().enqueue(run_job, job_id, worker_tasks.always_fails, {})

    _drain_queue()

    job = get_job(job_id)
    assert job.status == "failed"
    assert job.error == "Wrong API Key"


def test_worker_log_description_never_carries_the_job_arguments():
    job_id = create_job("seedance", {})
    rq_job = enqueue(
        run_job,
        (job_id, worker_tasks.make_result, {"name": "secret-api-key"}),
    )

    assert rq_job.description == "ofmhelpers.features.jobs.runner.run_job"
    _drain_queue()
    assert get_job(job_id).status == "done"


def _queue_run(queue: str) -> tuple[str, str]:
    job_id = create_job("clone_links", {})
    rq_job = enqueue_to(
        queue,
        run_job,
        (job_id, worker_tasks.make_result, {"name": "out.mp4"}),
    )
    return job_id, rq_job.id


def test_a_clone_links_run_waits_and_runs_on_its_own_queue():
    job_id, _ = _queue_run(QueueName.CLONE_LINKS)

    assert get_queue().get_job_ids() == []
    assert active_job_ids() == {job_id}

    SimpleWorker([get_queue(QueueName.CLONE_LINKS)], connection=get_redis()).work(
        burst=True,
    )
    assert get_job(job_id).status == "done"


def test_a_job_a_worker_is_taking_off_the_queue_still_counts(monkeypatch):
    monkeypatch.setenv("OFM_RECOVERY_ORPHAN_GRACE_S", "0")
    default_id, _ = _queue_run(QueueName.DEFAULT)
    links_id, _ = _queue_run(QueueName.CLONE_LINKS)
    for name in (QueueName.DEFAULT, QueueName.CLONE_LINKS):
        queue = get_queue(name)
        get_redis().lmove(queue.key, queue.intermediate_queue_key)
        assert queue.get_job_ids() == []

    assert active_job_ids() == {default_id, links_id}
    assert recovery.fail_cut_off(active_job_ids(), set()) == {}
    assert get_job(default_id).status == "running"
    assert get_job(links_id).status == "running"


def test_a_job_a_live_worker_runs_counts():
    job_id, rq_id = _queue_run(QueueName.CLONE_LINKS)
    queue = get_queue(QueueName.CLONE_LINKS)
    get_redis().lrem(queue.key, 1, rq_id)
    worker = Worker([queue], connection=get_redis())
    worker.register_birth()
    worker.prepare_execution(Job.fetch(rq_id, connection=get_redis()))

    assert active_job_ids() == {job_id}

    worker.register_death()
    assert active_job_ids() == set()
