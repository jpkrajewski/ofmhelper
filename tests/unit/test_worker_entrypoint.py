# Copyright (C) 2026 jpkrajewski

import inspect
import logging
import multiprocessing
import os
import signal
import sys
import time
from typing import ClassVar

import pytest
from rq import Worker

from ofmhelpers.background import worker as worker_module
from ofmhelpers.background.worker import ConfiguredWorker, QueueGroup
from ofmhelpers.cache import QueueName


def test_configured_worker_is_a_real_rq_worker():
    assert issubclass(ConfiguredWorker, Worker)


def test_perform_job_takes_what_rq_passes_it():
    ours = inspect.signature(ConfiguredWorker.perform_job).parameters
    rqs = inspect.signature(Worker.perform_job).parameters
    assert list(ours) == list(rqs)


def test_work_configures_logging_in_its_own_process(monkeypatch):
    calls = []
    monkeypatch.setattr(
        worker_module,
        "configure_logging",
        lambda *a, **kw: calls.append(1),
    )
    monkeypatch.setattr(Worker, "work", lambda self, *a, **kw: True)

    w = ConfiguredWorker.__new__(ConfiguredWorker)
    w.work(burst=True)

    assert calls, "ConfiguredWorker.work() must configure logging in its process"


def test_work_forwards_its_arguments_to_rq(monkeypatch):
    seen = {}
    monkeypatch.setattr(worker_module, "configure_logging", lambda *a, **kw: None)
    monkeypatch.setattr(
        Worker,
        "work",
        lambda self, burst, logging_level, date_format, log_format, max_jobs, max_idle_time, with_scheduler, dequeue_strategy: (  # noqa: E501
            seen.update(
                burst=burst,
                with_scheduler=with_scheduler,
                logging_level=logging_level,
            )
            or True
        ),
    )

    w = ConfiguredWorker.__new__(ConfiguredWorker)
    w.work(burst=True, with_scheduler=True, logging_level="DEBUG")

    assert seen["burst"] is True
    assert seen["with_scheduler"] is True
    assert seen["logging_level"] == "DEBUG"


class _Pool:
    made: ClassVar[list[_Pool]] = []
    niced: ClassVar[list[int]] = []

    def __init__(self, queues, connection, num_workers, worker_class):
        self.queues, self.connection = queues, connection
        self.num_workers, self.worker_class = num_workers, worker_class
        self.started = False
        _Pool.made.append(self)

    def start(self):
        self.started = True


def _pool(monkeypatch, *argv: str) -> _Pool:
    _Pool.made.clear()
    _Pool.niced.clear()
    monkeypatch.setattr(worker_module, "configure_logging", lambda *a, **kw: None)
    monkeypatch.setattr(worker_module, "WorkerPool", _Pool)
    monkeypatch.setattr(worker_module.os, "nice", _Pool.niced.append)
    monkeypatch.setattr(sys, "argv", ["ofmhelpers.background.worker", *argv])
    worker_module.main()
    (pool,) = _Pool.made
    assert pool.started
    return pool


def test_the_pool_runs_our_worker_class_on_the_configured_redis(monkeypatch):
    monkeypatch.setenv("OFM_REDIS_URL", "redis://example:6379/3")

    pool = _pool(monkeypatch)

    assert pool.worker_class is ConfiguredWorker
    kwargs = pool.connection.connection_pool.connection_kwargs
    assert (kwargs["host"], kwargs["db"]) == ("example", 3)


def test_the_clone_links_queue_has_its_own_pool(monkeypatch):
    monkeypatch.setenv("OFM_RQ_WORKERS", "10")
    monkeypatch.setenv("OFM_CLONE_LINKS_WORKERS", "4")

    pool = _pool(monkeypatch, QueueName.CLONE_LINKS)

    assert pool.queues == ["clone-links"]
    assert pool.num_workers == 4


def test_the_media_queue_is_one_process_so_one_encode_at_a_time(monkeypatch):
    pool = _pool(monkeypatch, "media")

    assert pool.queues == ["media"]
    assert pool.num_workers == 1


def test_the_default_pool_serves_only_the_default_queue(monkeypatch):
    monkeypatch.setenv("OFM_RQ_WORKERS", "10")

    pool = _pool(monkeypatch)

    assert pool.queues == ["default"]
    assert pool.num_workers == 10


def test_the_default_queue_runs_on_top_and_the_media_queue_at_the_bottom(monkeypatch):
    _pool(monkeypatch)
    assert _Pool.niced == [0]

    _pool(monkeypatch, "media")
    assert _Pool.niced == [19]


def test_an_unknown_queue_is_refused(monkeypatch, capsys):
    with pytest.raises(SystemExit):
        _pool(monkeypatch, "clone_links")

    assert "clone_links" in capsys.readouterr().err
    assert _Pool.made == []


def test_worker_logger_is_named_for_the_module_not_main():
    assert worker_module.logger.name == "ofmhelpers.background.worker"
    assert isinstance(worker_module.logger, logging.Logger)


def _group(monkeypatch, tmp_path, pool) -> multiprocessing.Process:
    monkeypatch.setattr(worker_module, "_preload_jobs", lambda: None)
    monkeypatch.setattr(
        worker_module.QueueWorkers,
        "run",
        lambda self: pool(self.queue, tmp_path),
    )
    queues = [QueueName.DEFAULT, QueueName.CLONE_LINKS, QueueName.EVENTS]
    group = multiprocessing.get_context("fork").Process(target=QueueGroup(queues).run)
    group.start()
    return group


def _serving(queue: QueueName, folder) -> None:
    signal.signal(signal.SIGTERM, lambda *_: os._exit(0))
    (folder / f"{queue.value}.pid").write_text(str(os.getpid()))
    time.sleep(30)


def _pools(folder, count: int) -> dict[str, int]:
    deadline = time.monotonic() + 10
    while True:
        found = {p.stem: p.read_text().strip() for p in folder.glob("*.pid")}
        if len(found) >= count and all(found.values()):
            return {queue: int(pid) for queue, pid in found.items()}
        assert time.monotonic() < deadline, "the pools did not start"
        time.sleep(0.01)


def _gone(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


def test_each_queue_of_a_group_has_its_own_pool_and_a_stop_reaches_them_all(
    monkeypatch,
    tmp_path,
):
    group = _group(monkeypatch, tmp_path, _serving)
    pools = _pools(tmp_path, 3)
    assert set(pools) == {"default", "clone-links", "events"}
    assert len(set(pools.values())) == 3

    assert group.pid is not None
    os.kill(group.pid, signal.SIGTERM)
    group.join(10)

    assert group.exitcode == 0
    assert all(_gone(pid) for pid in pools.values())


def _events_dies(queue: QueueName, folder) -> None:
    if queue is QueueName.EVENTS:
        (folder / "events.pid").write_text(str(os.getpid()))
        os._exit(3)
    _serving(queue, folder)


def test_a_pool_that_dies_takes_the_container_down_to_be_started_again(
    monkeypatch,
    tmp_path,
):
    group = _group(monkeypatch, tmp_path, _events_dies)
    pools = _pools(tmp_path, 3)

    group.join(10)

    assert group.exitcode == 1
    assert all(_gone(pid) for pid in pools.values())
