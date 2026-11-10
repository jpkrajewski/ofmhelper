"""
Covers ofmhelpers/worker.py, the RQ worker container's entrypoint.

The thing worth pinning is easy to break and fails silently: RQ's WorkerPool
runs each worker in its OWN child process, so logging configured in the pool
parent never reaches them. ConfiguredWorker is what fixes that, and it only
takes effect if the pool is actually given it.
"""

import inspect
import logging
import sys
from typing import ClassVar

import pytest
from rq import Worker

from ofmhelpers import worker as worker_module
from ofmhelpers.cache import QueueName
from ofmhelpers.worker import ConfiguredWorker


def test_configured_worker_is_a_real_rq_worker():
    """rq's WorkerPool runs it, so rq must accept it as a Worker."""
    assert issubclass(ConfiguredWorker, Worker)


def test_perform_job_takes_what_rq_passes_it():
    """rq's horse calls `perform_job` with rq's own parameters; an override
    left on an older signature fails every job (rq 3.0 added `execution`)."""
    ours = inspect.signature(ConfiguredWorker.perform_job).parameters
    rqs = inspect.signature(Worker.perform_job).parameters
    assert list(ours) == list(rqs)


def test_work_configures_logging_in_its_own_process(monkeypatch):
    """The whole point of the subclass: each forked child installs the app's
    logging config before it starts pulling jobs."""
    calls = []
    monkeypatch.setattr(
        worker_module, "configure_logging", lambda *a, **kw: calls.append(1)
    )
    # Stop before rq's real work loop -- we only care that logging was set up
    # first, not that a worker actually runs.
    monkeypatch.setattr(Worker, "work", lambda self, *a, **kw: True)

    # Bypass Worker.__init__ (it demands a live Redis connection); this test
    # is about the work() override, not worker construction.
    w = ConfiguredWorker.__new__(ConfiguredWorker)
    w.work(burst=True)

    assert calls, "ConfiguredWorker.work() must configure logging in its process"


def test_work_forwards_its_arguments_to_rq(monkeypatch):
    """The override must stay transparent -- rq calls work() with burst,
    logging_level, with_scheduler etc. and all of it has to survive."""
    seen = {}
    monkeypatch.setattr(worker_module, "configure_logging", lambda *a, **kw: None)
    monkeypatch.setattr(
        Worker, "work", lambda self, *a, **kw: seen.update(args=a, kwargs=kw) or True
    )

    w = ConfiguredWorker.__new__(ConfiguredWorker)
    w.work(burst=True, with_scheduler=True, logging_level="DEBUG")

    assert seen["kwargs"]["burst"] is True
    assert seen["kwargs"]["with_scheduler"] is True
    assert seen["kwargs"]["logging_level"] == "DEBUG"


class _Pool:
    """Stands in for RQ's WorkerPool: what it was given, and that it started."""

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
    """The pool `python -m ofmhelpers.worker <argv>` starts."""
    _Pool.made.clear()
    _Pool.niced.clear()
    monkeypatch.setattr(worker_module, "configure_logging", lambda *a, **kw: None)
    monkeypatch.setattr(worker_module, "WorkerPool", _Pool)
    monkeypatch.setattr(worker_module.os, "nice", _Pool.niced.append)
    monkeypatch.setattr(sys, "argv", ["ofmhelpers.worker", *argv])
    worker_module.main()
    (pool,) = _Pool.made
    assert pool.started
    return pool


def test_the_pool_runs_our_worker_class_on_the_configured_redis(monkeypatch):
    """Without it, children fall back to rq's own Worker and job logs silently
    revert to rq's format."""
    monkeypatch.setenv("OFM_REDIS_URL", "redis://example:6379/3")

    pool = _pool(monkeypatch)

    assert pool.worker_class is ConfiguredWorker
    kwargs = pool.connection.connection_pool.connection_kwargs
    assert (kwargs["host"], kwargs["db"]) == ("example", 3)


def test_the_clone_links_queue_has_its_own_pool(monkeypatch):
    """A 120-link /clone-links Generate runs on its own queue and pool
    (compose clone-links-worker), never on the default workers."""
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
    """Under `python -m ofmhelpers.worker`, __name__ is "__main__" -- a
    useless name to see in a log line."""
    assert worker_module.logger.name == "ofmhelpers.worker"
    assert isinstance(worker_module.logger, logging.Logger)
