"""The background sweeps the web process runs, started and stopped by
`web/main.py`'s lifespan (FastAPI's lifespan events).

Each sweep is a blocking function run in a thread, again `interval_s()`
after its previous run ended -- read fresh every round, so a settings change
applies at the next one; a sweep without an interval runs once, at startup.
A failed run is logged and the next one still comes: a broken sweep (Redis
down, Drive refusing) never takes its loop down. A new sweep is one entry in
`sweeps()`.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from typing import Any

from pydantic import BaseModel, ConfigDict

from ofmhelpers.config import settings
from ofmhelpers.log import get_logger
from ofmhelpers.web import (
    account_tracker,
    drive_folders,
    link_sync,
    preview_backfill,
    recovery,
    reel_watch,
)

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
        # jobs a restart cut off -- web/recovery.py.
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
        # Gallery previews for jobs older than previews: one pass.
        Sweep(name="preview backfill", run=preview_backfill.backfill),
    ]


async def _loop(sweep: Sweep) -> None:
    while True:
        if sweep.enabled():
            try:
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
