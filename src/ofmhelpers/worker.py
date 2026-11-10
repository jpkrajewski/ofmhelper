"""
Entrypoint for the RQ worker containers: `python -m ofmhelpers.worker [queue]`,
a `QueueName` (the default queue if none is named). One compose service per queue.

`QueueWorkers` runs RQ's own `WorkerPool` (its signal handling and graceful
shutdown, no pool of ours) with the processes and priority
`settings.infra.queues` gives the queue, each process a `ConfiguredWorker`: the
pool's children are separate processes, so without it job code runs with logging
never configured -- every record from a background job fell back to logging's
default handler and came out in a different shape from the API's, or (below
WARNING) not at all.
"""

import argparse
import importlib
import os

import logfire
from rq import Worker
from rq.worker_pool import WorkerPool
lazy from rq import Queue
lazy from rq.executions import Execution
lazy from rq.job import Job

from ofmhelpers.cache import QueueJob, QueueName, get_redis
from ofmhelpers.config import settings
from ofmhelpers.log import configure_logging, get_logger
from ofmhelpers.telemetry import configure_telemetry, flush_telemetry
lazy from ofmhelpers.integrations.ai_providers.base import provider_classes
lazy from ofmhelpers.integrations.llm.base import llm_classes

# Not __name__: under `python -m ofmhelpers.worker` that's "__main__", which
# tells you nothing in a log line.
logger = get_logger("ofmhelpers.worker")


class ConfiguredWorker(Worker):
    """A Worker that installs the app's logging config in its OWN process.

    `WorkerPool` runs each worker in a separate child process (see
    rq.worker_pool.run_worker), so configure_logging() in the parent never
    reaches them -- and Worker.work() then calls rq's setup_loghandlers(),
    which installs rq's own format because the fresh child has no handler
    yet. Without this subclass every *job* log line (the ones that actually
    matter) would come out in a different format from the rest of the app.
    """

    def work(self, *args, **kwargs) -> bool:
        configure_telemetry("ascend-worker")
        configure_logging()
        return super().work(*args, **kwargs)

    def perform_job(self, job: Job, queue: Queue, execution: Execution) -> bool:
        """Runs in the forked work horse, which rq ends with `os._exit` --
        no atexit, so the job's buffered spans and logs are sent here or
        never. `execution`: rq's record of this one run, passed on."""
        taken = QueueJob.taken(job, queue)
        try:
            with logfire.span("rq {func}", **taken.model_dump(mode="json")) as span:
                performed = super().perform_job(job, queue, execution)
                if not performed:
                    span.set_level("error")
                return performed
        finally:
            flush_telemetry()


_LAZY_HEAVY = ("google.genai", "googleapiclient.discovery", "numpy", "yt_dlp")


def _preload_jobs() -> None:
    """Every job's code, imported once in the pool's parent: its workers and
    their work horses are forks of it (rq 3.0 `ForkProcess`) and start with
    it. Without it each horse imported the whole app itself when RQ looked up
    its function (`ofmhelpers.web.routers...`): ~4 s before every job's work
    started, on every queue, prod and dev (Logfire 2026-11-07: `rq {func}`
    span to `job {task}` span 3.8-4.4 s; py-spy: the horse in imports).

    `_LAZY_HEAVY` are `lazy import`s the web process never pays for; a job would
    (Gemini 0.6 s, Drive 0.2 s), in every horse. Loaded here they cost once."""
    importlib.import_module("ofmhelpers.web.routers")
    for module in _LAZY_HEAVY:
        importlib.import_module(module)
    # The registries fill on the first lookup, which was in every horse too
    # (0.3-0.4 s a generation job, reviewed on 3.15, 2026-11-09).
    provider_classes()
    llm_classes()


class QueueWorkers:
    """The worker processes of one queue."""

    def __init__(self, queue: QueueName) -> None:
        self.queue = queue

    def run(self) -> None:
        """Until a shutdown signal; `compose stop` lets each finish its job."""
        config = settings.infra.queues[self.queue]
        logger.info(
            "starting rq worker-pool: %d workers, queue=%r, priority=%s",
            config.workers,
            self.queue.value,
            config.priority.name,
        )
        # The workers and the ffmpeg they start inherit it.
        os.nice(config.priority)
        _preload_jobs()
        WorkerPool(
            [self.queue.value],
            connection=get_redis(),
            num_workers=config.workers,
            worker_class=ConfiguredWorker,
        ).start()


def main() -> None:
    configure_telemetry("ascend-worker")
    configure_logging()
    parser = argparse.ArgumentParser(prog="ofmhelpers.worker")
    parser.add_argument(
        "queue",
        nargs="?",
        type=QueueName,
        choices=list(QueueName),
        default=QueueName.DEFAULT,
    )
    QueueWorkers(parser.parse_args().queue).run()


if __name__ == "__main__":
    main()
