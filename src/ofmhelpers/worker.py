"""
Entrypoint for the RQ worker container: `python -m ofmhelpers.worker`.

Exists purely so the worker gets the same logging setup the API does. The
compose file used to invoke `rq worker-pool` directly, which meant job code
ran with logging never configured -- every record from a background job fell
back to logging's default handler and came out in a different shape from the
API's, or (below WARNING) not at all.

Everything else -- pool size, queue, timeouts -- still comes from RQ's own
CLI and from settings.infra, so this stays a thin shim rather than a second
place worker behavior is defined.
"""

from __future__ import annotations

import sys

from rq import Worker
from rq.cli import main as rq_main

from ofmhelpers.config import settings
from ofmhelpers.log import configure_logging, get_logger

# Not __name__: under `python -m ofmhelpers.worker` that's "__main__", which
# tells you nothing in a log line.
logger = get_logger("ofmhelpers.worker")


class ConfiguredWorker(Worker):
    """A Worker that installs the app's logging config in its OWN process.

    `rq worker-pool` runs each worker in a separate child process (see
    rq.worker_pool.run_worker), so configure_logging() in the parent never
    reaches them -- and Worker.work() then calls rq's setup_loghandlers(),
    which installs rq's own format because the fresh child has no handler
    yet. Without this subclass every *job* log line (the ones that actually
    matter) would come out in a different format from the rest of the app.
    """

    def work(self, *args, **kwargs) -> bool:
        configure_logging()
        return super().work(*args, **kwargs)


def main() -> None:
    configure_logging()

    # Seeds the first midnight-UTC instagram-stats sweep via RQ's OWN
    # scheduler (a separate OS process -- see rq.scheduler, already started
    # by `rq worker-pool` below) rather than a thread in this process.
    #
    # A `threading.Thread` running here used to do this instead, and it
    # silently wedged every subsequent job: `rq worker-pool` forks a fresh
    # OS process per job (Worker.execute_job -> fork_work_horse), and
    # fork()ing a process that has an extra live thread is unsafe -- any
    # lock that thread held at the exact moment of fork (GIL bookkeeping,
    # the import lock, a library's internal lock) stays locked forever in
    # the child. Confirmed live: jobs hung indefinitely (STARTED forever,
    # no exception, no subprocess spawned) only after that thread existed;
    # a plain one-shot call here, with no persistent thread, doesn't
    # perturb the process rq forks from at all.
    #
    # Non-fatal: a broker that's not reachable *yet* must not stop the pool
    # from starting -- rq's own connection handling retries, and a sweep gets
    # rescheduled at the end of every run anyway.
    from ofmhelpers.scraping.instagram_stats_job import ensure_scheduled

    try:
        ensure_scheduled()
    except Exception:
        logger.exception("could not schedule the instagram stats sweep at boot")

    infra = settings.infra
    workers = infra.rq_workers
    logger.info(
        "starting rq worker-pool: %d workers, queue=%r",
        workers,
        "default",
    )

    # Hand off to RQ's own CLI so every flag it supports keeps working, and
    # so we inherit its signal handling / graceful shutdown rather than
    # reimplementing a pool here.
    sys.argv = [
        "rq",
        "worker-pool",
        "--num-workers",
        str(workers),
        "--url",
        infra.redis_url,
        # Dotted path, not the class object: worker-pool spawns children by
        # importing this name in the new process.
        "--worker-class",
        "ofmhelpers.worker.ConfiguredWorker",
    ]
    rq_main()


if __name__ == "__main__":
    main()
