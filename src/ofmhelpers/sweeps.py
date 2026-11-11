"""Entrypoint of the sweeps container: `python -m ofmhelpers.sweeps` runs every
background sweep (`features/jobs/sweeps.py` `sweeps()`) until it is told to
stop. One process, so each sweep runs once however many web processes serve:
until 2026-11-11 the web app started them in its lifespan, and a second web
process would have run every one of them twice.

One Python process and threads, no pool: a sweep looks, queues and waits on the
network; the work goes to the workers. On SIGTERM (`compose stop`, every
deploy) each sweep is cancelled and awaited; a round already in its thread
finishes (the Telegram read waits up to `OFM_TELEGRAM_ASK_WAIT_S`), which is
what the service's `stop_grace_period` is for.
"""

import asyncio
import signal

from ofmhelpers.features.jobs.sweeps import Sweep, running, sweeps
from ofmhelpers.log import configure_logging, get_logger
from ofmhelpers.telemetry import configure_telemetry, flush_telemetry

# Not __name__: under `python -m ofmhelpers.sweeps` that's "__main__", which
# tells you nothing in a log line.
logger = get_logger("ofmhelpers.sweeps")


async def run(all_sweeps: list[Sweep]) -> None:
    """`all_sweeps` until SIGTERM or SIGINT, then stopped cleanly."""
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, stop.set)
    async with running(all_sweeps):
        logger.info("sweeps running: %d", len(all_sweeps))
        await stop.wait()
        logger.info("asked to stop: stopping the sweeps")
    logger.info("sweeps stopped")


def main() -> None:
    configure_telemetry("ascend-sweeps")
    configure_logging()
    try:
        asyncio.run(run(sweeps()))
    finally:
        flush_telemetry()


if __name__ == "__main__":
    main()
