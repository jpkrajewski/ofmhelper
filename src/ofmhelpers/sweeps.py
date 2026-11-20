import asyncio
import signal

from ofmhelpers.features.jobs.sweeps import Sweep, running, sweeps
from ofmhelpers.log import configure_logging, get_logger
from ofmhelpers.sentry import configure_sentry
from ofmhelpers.telemetry import configure_telemetry, flush_telemetry

logger = get_logger("ofmhelpers.sweeps")


async def run(all_sweeps: list[Sweep]) -> None:
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
    configure_sentry("ascend-sweeps")
    configure_logging()
    try:
        asyncio.run(run(sweeps()))
    finally:
        flush_telemetry()


if __name__ == "__main__":
    main()
