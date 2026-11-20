import argparse
import importlib
import multiprocessing
import os
import signal
import sys
from multiprocessing.connection import wait

import logfire
from rq import Worker
from rq.worker_pool import WorkerPool
lazy from rq import Queue
lazy from rq.executions import Execution
lazy from rq.job import Job

from ofmhelpers.cache import QueueJob, QueueName, get_redis
from ofmhelpers.config import settings
from ofmhelpers.db.acting import Acting, Via, acting
from ofmhelpers.log import configure_logging, get_logger
from ofmhelpers.sentry import configure_sentry
from ofmhelpers.telemetry import configure_telemetry, flush_telemetry
lazy from ofmhelpers.integrations.ai_providers.base import provider_classes
lazy from ofmhelpers.integrations.llm.base import llm_classes

logger = get_logger("ofmhelpers.worker")


class ConfiguredWorker(Worker):
    def work(self, *args, **kwargs) -> bool:
        configure_telemetry("ascend-worker")
        configure_sentry("ascend-worker")
        configure_logging()
        return super().work(*args, **kwargs)

    def perform_job(self, job: Job, queue: Queue, execution: Execution) -> bool:
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
    importlib.import_module("ofmhelpers.web.routers")
    importlib.import_module("ofmhelpers.features.jobs.runner")
    for module in _LAZY_HEAVY:
        importlib.import_module(module)
    provider_classes()
    llm_classes()


class QueueWorkers:
    def __init__(self, queue: QueueName) -> None:
        self.queue = queue

    def run(self) -> None:
        config = settings.infra.queues[self.queue]
        logger.info(
            "starting rq worker-pool: %d workers, queue=%r, priority=%s",
            config.workers,
            self.queue.value,
            config.priority.name,
        )
        os.nice(config.priority)
        _preload_jobs()
        WorkerPool(
            [self.queue.value],
            connection=get_redis(),
            num_workers=config.workers,
            worker_class=ConfiguredWorker,
        ).start()


class QueueGroup:
    def __init__(self, queues: list[QueueName]) -> None:
        self.queues = queues

    def run(self) -> None:
        _preload_jobs()
        fork = multiprocessing.get_context("fork")
        pools = [
            fork.Process(target=QueueWorkers(queue).run, name=f"pool {queue.value}")
            for queue in self.queues
        ]
        for pool in pools:
            pool.start()
        asked = False

        def stop(signum: int, _frame: object) -> None:
            nonlocal asked
            asked = True
            _signal_all(pools, signum)

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        wait([pool.sentinel for pool in pools])
        if not asked:
            logger.error("a worker pool ended by itself: stopping the others")
            _signal_all(pools, signal.SIGTERM)
        for pool in pools:
            pool.join()
        sys.exit(0 if asked else 1)


def _signal_all(pools: list, signum: int) -> None:
    for pool in pools:
        if pool.pid is not None and pool.is_alive():
            os.kill(pool.pid, signum)


def main() -> None:
    configure_telemetry("ascend-worker")
    configure_sentry("ascend-worker")
    configure_logging()
    parser = argparse.ArgumentParser(prog="ofmhelpers.worker")
    parser.add_argument("queue", nargs="*", type=QueueName, choices=list(QueueName))
    queues = parser.parse_args().queue or [QueueName.DEFAULT]
    names = ",".join(q.value for q in queues)
    with acting(Acting(via=Via.WORKER, ref=f"worker {names}")):
        if len(queues) == 1:
            QueueWorkers(queues[0]).run()
        else:
            QueueGroup(queues).run()


if __name__ == "__main__":
    main()
