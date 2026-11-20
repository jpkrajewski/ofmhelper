import asyncio
import signal
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from functools import partial
from typing import Any

from pydantic import BaseModel, ConfigDict

from ofmhelpers.acting import Acting, Via, acting
from ofmhelpers.background import recovery
from ofmhelpers.background.alive import report_alive
from ofmhelpers.cache import JobQueue, QueueName
from ofmhelpers.config import settings
from ofmhelpers.config.retired import retired
from ofmhelpers.features.account import telegram as telegram_link
from ofmhelpers.features.accounts import tracker as account_tracker
from ofmhelpers.features.chatting import sources as chat_sources
from ofmhelpers.features.chatting import sync as chat_history
from ofmhelpers.features.chatting import wait_events as chat_wait_events
from ofmhelpers.features.chatting import webhooks as chat_webhooks
from ofmhelpers.features.distribution import sweep as distribution_sweep
from ofmhelpers.features.downloads import cleanup as downloads_cleanup
from ofmhelpers.features.drive import folders as drive_folders
from ofmhelpers.features.edit import cleanup as edit_cleanup
from ofmhelpers.features.fakes import install_fakes
from ofmhelpers.features.idempotency import service as idempotency
from ofmhelpers.features.jobs import raw_files
from ofmhelpers.features.links import sync as link_sync
from ofmhelpers.features.phones import backup as phones_backup
from ofmhelpers.features.previews import backfill as preview_backfill
from ofmhelpers.features.reels import watch as reel_watch
from ofmhelpers.features.reports import asks as report_asks
from ofmhelpers.features.reports import daily as daily_report
from ofmhelpers.features.trials import cleanup as trials_cleanup
from ofmhelpers.features.users import idle as va_idle
from ofmhelpers.features.viral import cleanup as viral_cleanup
from ofmhelpers.log import configure_logging, get_logger
from ofmhelpers.monitoring.sentry import HEARTBEAT_S, configure_sentry
from ofmhelpers.monitoring.telemetry import configure_telemetry, flush_telemetry

logger = get_logger(__name__)


def _always() -> bool:
    return True


class Sweep(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    run: Callable[[], Any]
    interval_s: Callable[[], float] | None = None
    enabled: Callable[[], bool] = _always
    queue: QueueName | None = None
    waits_first: bool = False

    def round(self) -> None:
        if self.queue is None:
            self.run()
        else:
            key = "sweep-" + "-".join(self.name.split())
            JobQueue(name=self.queue).put_once(key, self.run)


def sweeps() -> list[Sweep]:
    return [
        Sweep(
            name="recovery",
            run=recovery.run_recovery_once,
            interval_s=lambda: settings.web.recovery_sweep_interval_s,
        ),
        Sweep(
            name="reel watch",
            run=reel_watch.queue_due_scans,
            interval_s=lambda: settings.instagram.rescan_check_interval_s,
            enabled=lambda: not retired().has("reels"),
        ),
        Sweep(
            name="account tracker",
            run=account_tracker.queue_due_scans,
            interval_s=lambda: settings.accounts.rescan_check_interval_s,
            enabled=lambda: settings.accounts.scheduled(settings.infra.production),
        ),
        Sweep(
            name="link sync",
            run=link_sync.queue_due_syncs,
            interval_s=lambda: settings.links.sync_check_interval_s,
        ),
        Sweep(
            name="distribution",
            run=distribution_sweep.run_round,
            interval_s=lambda: settings.distribution.check_interval_s,
        ),
        Sweep(
            name="drive",
            run=drive_folders.sync,
            interval_s=lambda: settings.gdrive.check_interval_s,
            enabled=lambda: (
                drive_folders.enabled()
                and settings.gdrive.sweeping(settings.infra.production)
            ),
        ),
        Sweep(
            name="chat webhooks",
            run=chat_webhooks.ensure_destinations,
            interval_s=lambda: settings.chatting.webhook_sync_interval_s,
            enabled=chat_webhooks.can_make_webhooks,
        ),
        *(
            Sweep(
                name=f"chat history {platform.value}",
                run=partial(chat_history.run_when_due, platform),
                interval_s=lambda: settings.chatting.check_interval_s,
                enabled=partial(chat_history.enabled, platform),
            )
            for platform in chat_sources.platforms()
        ),
        Sweep(
            name="chat wait events",
            run=chat_wait_events.emit_waiting,
            interval_s=lambda: settings.chatting.wait_events_check_s,
        ),
        Sweep(
            name="chat wait rechecks",
            run=chat_wait_events.recheck_due,
            interval_s=lambda: settings.chatting.wait_recheck_poll_s,
        ),
        Sweep(
            name="daily report",
            run=daily_report.run,
            interval_s=lambda: settings.reports.check_s,
            enabled=lambda: settings.reports.daily_enabled,
        ),
        Sweep(
            name="telegram asks",
            run=report_asks.answer_asks,
            interval_s=report_asks.pause_s,
            enabled=telegram_link.enabled,
        ),
        Sweep(
            name="edit cleanup",
            run=edit_cleanup.remove_old,
            interval_s=lambda: settings.web.edit_sweep_s,
            queue=QueueName.PREVIEWS,
        ),
        Sweep(
            name="trials cleanup",
            run=trials_cleanup.remove_old,
            interval_s=lambda: settings.trials.sweep_s,
            queue=QueueName.PREVIEWS,
        ),
        Sweep(
            name="downloads cleanup",
            run=downloads_cleanup.remove_old,
            interval_s=lambda: settings.downloaders.sweep_s,
            enabled=lambda: settings.downloaders.sweeping(settings.infra.production),
            queue=QueueName.PREVIEWS,
        ),
        Sweep(
            name="viral cleanup",
            run=viral_cleanup.remove_old,
            interval_s=lambda: settings.viral.sweep_s,
            queue=QueueName.PREVIEWS,
        ),
        Sweep(
            name="raw files",
            run=raw_files.remove_old,
            interval_s=lambda: settings.aigen.raw_sweep_s,
            queue=QueueName.PREVIEWS,
        ),
        Sweep(
            name="phones backup",
            run=phones_backup.dump,
            interval_s=lambda: settings.vault.backup_sweep_s,
            enabled=lambda: bool(settings.vault.key_list()),
        ),
        Sweep(
            name="sentry heartbeat",
            run=report_alive,
            interval_s=lambda: HEARTBEAT_S,
            waits_first=True,
            enabled=lambda: settings.sentry.dsn is not None,
        ),
        Sweep(
            name="idempotency keys",
            run=idempotency.remove_old,
            interval_s=lambda: settings.idempotency.sweep_s,
        ),
        Sweep(
            name="va idle",
            run=va_idle.end_idle,
            interval_s=lambda: settings.web.va_idle_sweep_s,
        ),
        Sweep(name="preview backfill", run=preview_backfill.backfill),
    ]


async def _loop(sweep: Sweep) -> None:
    with acting(Acting(via=Via.SWEEP, ref=sweep.name)):
        if sweep.waits_first and sweep.interval_s is not None:
            await asyncio.sleep(sweep.interval_s())
        while True:
            try:
                if sweep.enabled():
                    await asyncio.to_thread(sweep.round)
            except Exception:
                logger.exception("%s sweep failed", sweep.name)
            if sweep.interval_s is None:
                return
            await asyncio.sleep(sweep.interval_s())


@asynccontextmanager
async def running(all_sweeps: list[Sweep]) -> AsyncGenerator[None]:
    tasks = [asyncio.create_task(_loop(s), name=f"sweep {s.name}") for s in all_sweeps]
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


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
    install_fakes()
    try:
        asyncio.run(run(sweeps()))
    finally:
        flush_telemetry()


if __name__ == "__main__":
    main()
