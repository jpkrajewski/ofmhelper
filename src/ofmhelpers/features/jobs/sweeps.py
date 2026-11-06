"""The background sweeps the web process runs, started and stopped by
`web/main.py`'s lifespan (FastAPI's lifespan events).

Each sweep is a blocking function run in a thread, again `interval_s()`
after its previous run ended -- read fresh every round, so a settings change
applies at the next one; a sweep without an interval runs once, at startup.
A failed run is logged and the next one still comes: a broken sweep (Redis
down, Drive refusing) never takes its loop down. A new sweep is one entry in
`sweeps()`.
"""

import asyncio
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from functools import partial
from typing import Any

from pydantic import BaseModel, ConfigDict

from ofmhelpers.config import settings
from ofmhelpers.features.accounts import tracker as account_tracker
from ofmhelpers.features.chatting import sources as chat_sources
from ofmhelpers.features.chatting import sync as chat_history
from ofmhelpers.features.chatting import wait_events as chat_wait_events
from ofmhelpers.features.chatting import webhooks as chat_webhooks
from ofmhelpers.features.drive import folders as drive_folders
from ofmhelpers.features.edit import cleanup as edit_cleanup
from ofmhelpers.features.jobs import recovery
from ofmhelpers.features.links import sync as link_sync
from ofmhelpers.features.previews import backfill as preview_backfill
from ofmhelpers.features.reels import watch as reel_watch
from ofmhelpers.features.reports import daily as daily_report
from ofmhelpers.log import get_logger

logger = get_logger(__name__)


def _always() -> bool:
    return True


class Sweep(BaseModel):
    """One background sweep: what runs, how long to wait between runs (None:
    once, at startup) and whether a round runs at all right now."""

    model_config = ConfigDict(frozen=True)

    name: str
    run: Callable[[], Any]
    interval_s: Callable[[], float] | None = None
    enabled: Callable[[], bool] = _always


def _drive_enabled() -> bool:
    """Drive is read through each owner's Google sign-in: nothing to sweep
    until Google sign-in is configured (or every outside service is faked)."""
    return settings.signin.google_configured or settings.infra.fake_externals


def sweeps() -> list[Sweep]:
    """Every sweep the web process runs."""
    return [
        # Finishes provider requests that outlived their job's poll, and
        # jobs a restart cut off -- features/jobs/recovery.py.
        Sweep(
            name="recovery",
            run=recovery.run_recovery_once,
            interval_s=lambda: settings.web.recovery_sweep_interval_s,
        ),
        # Rescans /reels' watched Instagram profiles when due.
        Sweep(
            name="reel watch",
            run=reel_watch.queue_due_scans,
            interval_s=lambda: settings.instagram.rescan_check_interval_s,
        ),
        # Rescans /accounts' tracked social accounts when due.
        Sweep(
            name="account tracker",
            run=account_tracker.queue_due_scans,
            interval_s=lambda: settings.accounts.rescan_check_interval_s,
        ),
        # Syncs /links' links and their clicks when due.
        Sweep(
            name="link sync",
            run=link_sync.queue_due_syncs,
            interval_s=lambda: settings.links.sync_check_interval_s,
        ),
        # Makes and reads /drive's folders in each owner's Google Drive.
        Sweep(
            name="drive",
            run=drive_folders.sync,
            interval_s=lambda: settings.gdrive.check_interval_s,
            enabled=_drive_enabled,
        ),
        # Each model's webhook destination, on a server with a public address.
        Sweep(
            name="chat webhooks",
            run=chat_webhooks.ensure_destinations,
            interval_s=lambda: settings.chatting.webhook_sync_interval_s,
            enabled=chat_webhooks.can_make_webhooks,
        ),
        # What webhooks missed, and all there is without them: /chatting and the
        # "Chats unanswered" pop-up read what it stores. One sweep per platform, so a
        # slow or hanging one does not hold up the next.
        *(
            Sweep(
                name=f"chat history {platform.value}",
                run=partial(chat_history.run_when_due, platform),
                interval_s=lambda: settings.chatting.check_interval_s,
                enabled=partial(chat_history.enabled, platform),
            )
            for platform in chat_sources.platforms()
        ),
        # "Chats unanswered" to Telegram every 15 min while fans wait, the newest list
        # replacing the one before.
        Sweep(
            name="chat wait events",
            run=chat_wait_events.emit_waiting,
            interval_s=lambda: settings.chatting.wait_events_check_s,
        ),
        # The daily report (14:00 Warsaw) to Telegram; a baseline the first time.
        Sweep(
            name="daily report",
            run=daily_report.run,
            interval_s=lambda: settings.reports.check_s,
            enabled=lambda: settings.reports.daily_enabled,
        ),
        # /edit's uploads, cuts and frame strips older than a day: nothing kept.
        Sweep(
            name="edit cleanup",
            run=edit_cleanup.remove_old,
            interval_s=lambda: settings.web.edit_sweep_s,
        ),
        # Gallery previews for jobs older than previews: one pass.
        Sweep(name="preview backfill", run=preview_backfill.backfill),
    ]


async def _loop(sweep: Sweep) -> None:
    while True:
        try:
            if sweep.enabled():
                await asyncio.to_thread(sweep.run)
        except Exception:
            logger.exception("%s sweep failed", sweep.name)
        if sweep.interval_s is None:
            return
        await asyncio.sleep(sweep.interval_s())


@asynccontextmanager
async def running(all_sweeps: list[Sweep]) -> AsyncGenerator[None]:
    """`all_sweeps` running for the duration of the block; on exit each is
    cancelled and awaited, so none outlives the app."""
    tasks = [asyncio.create_task(_loop(s), name=f"sweep {s.name}") for s in all_sweeps]
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
