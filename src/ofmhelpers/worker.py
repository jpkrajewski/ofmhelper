"""
Entrypoint for the RQ worker containers: `python -m ofmhelpers.worker
[queue]` -- the default queue (generations, downloads, ...) with
`rq_workers` processes, or `previews` (gallery previews, web/previews.py)
with `preview_workers`.

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

from ofmhelpers.cache import PREVIEWS_QUEUE, QUEUE_NAME
from ofmhelpers.config import settings
from ofmhelpers.log import configure_logging, get_logger
from ofmhelpers.telemetry import configure_telemetry, flush_telemetry

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
        configure_telemetry("ascend-worker")
        configure_logging()
        return super().work(*args, **kwargs)

    def perform_job(self, job, queue) -> bool:
        """Runs in the forked work horse, which rq ends with `os._exit` --
        no atexit, so the job's buffered spans and logs are sent here or
        never."""
        try:
            return super().perform_job(job, queue)
        finally:
            flush_telemetry()


def main() -> None:
    configure_telemetry("ascend-worker")
    configure_logging()

    infra = settings.infra
    queue = sys.argv[1] if len(sys.argv) > 1 else QUEUE_NAME
    pools = {QUEUE_NAME: infra.rq_workers, PREVIEWS_QUEUE: infra.preview_workers}
    if queue not in pools:
        msg = f"unknown queue {queue!r}; one of {sorted(pools)}"
        raise SystemExit(msg)
    workers = pools[queue]
    logger.info("starting rq worker-pool: %d workers, queue=%r", workers, queue)

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
        queue,
    ]
    rq_main()


if __name__ == "__main__":
    main()
