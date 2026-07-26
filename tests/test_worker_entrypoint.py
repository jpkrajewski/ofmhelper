"""
Covers ofmhelpers/worker.py, the RQ worker container's entrypoint.

The thing worth pinning is easy to break and fails silently: `rq worker-pool`
runs each worker in its OWN child process, so logging configured in the pool
parent never reaches them. ConfiguredWorker is what fixes that, and it only
takes effect if main() actually passes --worker-class.
"""

import logging

from rq import Worker

from ofmhelpers import worker as worker_module
from ofmhelpers.worker import ConfiguredWorker


def test_configured_worker_is_a_real_rq_worker():
    """It's passed to rq by dotted path, so rq must accept it as a Worker."""
    assert issubclass(ConfiguredWorker, Worker)


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


def test_main_passes_our_worker_class_and_settings_to_rq(monkeypatch):
    """Without --worker-class, children fall back to rq's own Worker and job
    logs silently revert to rq's format."""
    monkeypatch.setenv("OFM_RQ_WORKERS", "7")
    monkeypatch.setenv("OFM_REDIS_URL", "redis://example:6379/3")
    monkeypatch.setattr(worker_module, "configure_logging", lambda *a, **kw: None)

    argv = {}
    monkeypatch.setattr(worker_module, "rq_main", lambda: argv.update(v=sys_argv()))

    def sys_argv():
        import sys

        return list(sys.argv)

    worker_module.main()

    cmd = argv["v"]
    assert cmd[:2] == ["rq", "worker-pool"]
    assert "--worker-class" in cmd
    assert cmd[cmd.index("--worker-class") + 1] == "ofmhelpers.worker.ConfiguredWorker"
    assert cmd[cmd.index("--num-workers") + 1] == "7"
    assert cmd[cmd.index("--url") + 1] == "redis://example:6379/3"


def test_worker_logger_is_named_for_the_module_not_main():
    """Under `python -m ofmhelpers.worker`, __name__ is "__main__" -- a
    useless name to see in a log line."""
    assert worker_module.logger.name == "ofmhelpers.worker"
    assert isinstance(worker_module.logger, logging.Logger)
