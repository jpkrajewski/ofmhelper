"""The FIFO job queues (cache/queue.py) against a real Redis and a real RQ
worker: first in first out, who is ahead of whom, a waiting job still counting as
alive, and what Logfire gets. Prod 2026-11-04: six 4K encodes at once on 7.75 GB,
the kernel killing them every 30 s; the media queue makes them wait their turn."""

import json

import pytest
import worker_tasks
from logfire.testing import CaptureLogfire
from rq import SimpleWorker, Worker

from ofmhelpers.cache import (
    JOB_QUEUES,
    JobQueue,
    QueueName,
    active_job_ids,
    get_queue,
    get_redis,
)
from ofmhelpers.config import settings
from ofmhelpers.config.settings import QueuePriority
from ofmhelpers.web.stores.jobs import create_job, run_job
from ofmhelpers.worker import ConfiguredWorker


@pytest.fixture(autouse=True)
def _async_broker(monkeypatch):
    # The suite runs jobs inline (OFM_RQ_ASYNC=false); these need the real queue.
    monkeypatch.setenv("OFM_RQ_ASYNC", "true")
    get_redis().flushdb()
    yield
    get_redis().flushdb()


class _InlineWorker(SimpleWorker, ConfiguredWorker):
    """The real `perform_job`, with SimpleWorker's no fork, and the test's
    logging left as it is (`ConfiguredWorker.work` configures its own process)."""

    def work(self, *args, **kwargs):
        return Worker.work(self, *args, **kwargs)


def _drain_media() -> None:
    _InlineWorker([get_queue(QueueName.MEDIA)], connection=get_redis()).work(burst=True)


def _waiting_job() -> str:
    return create_job("seedance", {})


def test_jobs_run_in_the_order_they_joined():
    media = JobQueue(QueueName.MEDIA)
    for name in ("first", "second", "third"):
        media.put(worker_tasks.note_down, name)

    _drain_media()

    assert get_redis().lrange("ran", 0, -1) == [b"first", b"second", b"third"]


def test_the_line_says_how_many_are_ahead_of_each_job():
    media = JobQueue(QueueName.MEDIA)
    first, second = _waiting_job(), _waiting_job()
    media.put(run_job, first, worker_tasks.make_result, {"name": "a.mp4"})
    media.put(run_job, second, worker_tasks.make_result, {"name": "b.mp4"})

    assert media.line() == {first: 0, second: 1}


def test_the_job_being_run_counts_as_ahead():
    media = JobQueue(QueueName.MEDIA)
    waiting = _waiting_job()
    media.put(worker_tasks.note_the_line)
    media.put(run_job, waiting, worker_tasks.make_result, {"name": "a.mp4"})

    _drain_media()

    assert json.loads(get_redis().get("line_seen")) == {waiting: 1}


def test_a_job_waiting_on_the_media_queue_is_not_taken_for_cut_off():
    """`active_job_ids` is what recovery asks before failing a running job."""
    job_id = _waiting_job()
    JobQueue(QueueName.MEDIA).put(
        run_job, job_id, worker_tasks.make_result, {"name": "a.mp4"}
    )

    assert job_id in active_job_ids()


def test_default_is_on_top_and_media_at_the_bottom_one_at_a_time():
    queues = settings.infra.queues
    assert list(queues) == list(QueueName)
    priorities = [queue.priority for queue in queues.values()]
    assert priorities == sorted(priorities)
    assert queues[QueueName.DEFAULT].priority == QueuePriority.HIGHEST
    assert queues[QueueName.MEDIA].priority == QueuePriority.LOWEST
    assert queues[QueueName.MEDIA].workers == 1
    assert QueueName.MEDIA in JOB_QUEUES


def test_a_cut_someone_waits_for_gets_the_cpu_before_exports_and_trials():
    """Prod 2026-11-06: at nice 15 next to four clone exports at nice 0, a 14 s
    1440p cut took 40 s."""
    queues = settings.infra.queues
    assert queues[QueueName.EDIT].priority == QueuePriority.HIGHEST
    assert queues[QueueName.EDIT].workers == 1
    assert queues[QueueName.EDIT].priority < queues[QueueName.MEDIA].priority


def test_joining_a_line_is_logged_without_the_jobs_arguments(capfire: CaptureLogfire):
    media = JobQueue(QueueName.MEDIA)
    media.put(worker_tasks.note_down, "before")
    job_id = _waiting_job()

    media.put(
        run_job, job_id, worker_tasks.make_result, {"name": "a", "key": "sk-secret"}
    )

    spans = capfire.exporter.exported_spans_as_dict()
    logged = [s["attributes"] for s in spans if s["name"].startswith("queue {queue}")]
    assert [(a["queue"], a["func"], a["ahead"]) for a in logged] == [
        ("media", "note_down", 0),
        ("media", "run_job", 1),
    ]
    assert logged[1]["rq_job_id"]
    assert "sk-secret" not in json.dumps(spans, default=str)


def test_a_run_says_its_queue_how_long_it_waited_and_who_still_waits(
    capfire: CaptureLogfire,
):
    media = JobQueue(QueueName.MEDIA)
    first = media.put(worker_tasks.note_down, "first")
    second = media.put(worker_tasks.note_down, "second")

    _drain_media()

    runs = [
        s["attributes"]
        for s in capfire.exporter.exported_spans_as_dict()
        if s["name"] == "rq {func}"
    ]
    assert [(r["queue"], r["waiting"]) for r in runs] == [("media", 1), ("media", 0)]
    assert [r["rq_job_id"] for r in runs] == [first.id, second.id]
    assert all(r["waited_s"] >= 0 for r in runs)
