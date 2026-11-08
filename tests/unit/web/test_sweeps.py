"""web/sweeps.py: the one runner behind every background sweep. A broken
sweep never takes its loop down, a gated round is skipped, and nothing
outlives the app."""

import asyncio
import threading
from collections.abc import Coroutine
from concurrent.futures import ThreadPoolExecutor

from ofmhelpers.features.chatting import sync as chat_history
from ofmhelpers.schemas.chatting import ChatPlatform
from ofmhelpers.web import sweeps
from ofmhelpers.web.sweeps import Sweep, running


def _run(scenario: Coroutine) -> None:
    """In a thread of its own: e2e tests in the same worker leave Playwright's
    event loop running in this one, and `asyncio.run` refuses to nest. A stuck
    runner fails on the timeout instead of hanging the suite."""
    with ThreadPoolExecutor(1) as pool:
        pool.submit(asyncio.run, asyncio.wait_for(scenario, timeout=5)).result()


def _until(seen: list, count: int, *all_sweeps: Sweep) -> None:
    """Run `all_sweeps` until `seen` has `count` entries."""

    async def scenario() -> None:
        async with running(list(all_sweeps)):
            while len(seen) < count:  # noqa: ASYNC110 -- filled from the sweep's thread
                await asyncio.sleep(0.01)

    _run(scenario())


def test_a_failed_run_is_logged_and_the_next_one_still_comes(caplog):
    ran = []

    def run() -> None:
        ran.append("run")
        if len(ran) == 1:
            raise RuntimeError

    _until(ran, 2, Sweep(name="flaky", run=run, interval_s=lambda: 0))

    assert "flaky sweep failed" in caplog.text


def test_a_failed_gate_is_logged_and_the_next_round_still_comes(caplog):
    """A bad setting read by `enabled` (a malformed daily time) costs that
    round, never the sweep."""
    checks = []
    ran = []

    def enabled() -> bool:
        checks.append("check")
        if len(checks) == 1:
            raise ValueError
        return True

    _until(
        ran,
        1,
        Sweep(
            name="gated",
            run=lambda: ran.append("run"),
            interval_s=lambda: 0,
            enabled=enabled,
        ),
    )

    assert "gated sweep failed" in caplog.text


def test_a_closed_gate_skips_the_round_and_keeps_checking():
    checks = []
    ran = []

    def enabled() -> bool:
        checks.append("check")
        return len(checks) >= 3

    _until(
        ran,
        1,
        Sweep(
            name="gated",
            run=lambda: ran.append(len(checks)),
            interval_s=lambda: 0,
            enabled=enabled,
        ),
    )

    assert ran[0] == 3


def test_a_sweep_without_an_interval_runs_once():
    once = []
    ticks = []

    _until(
        ticks,
        5,
        Sweep(name="once", run=lambda: once.append("run")),
        Sweep(name="ticks", run=lambda: ticks.append("run"), interval_s=lambda: 0),
    )

    assert once == ["run"]


def test_every_sweep_is_cancelled_and_awaited_on_exit():
    left = []

    async def scenario() -> None:
        sweep = Sweep(name="ticks", run=lambda: None, interval_s=lambda: 3600)
        async with running([sweep]):
            await asyncio.sleep(0)
        left.extend(
            task.get_name()
            for task in asyncio.all_tasks()
            if task.get_name().startswith("sweep ")
        )

    _run(scenario())

    assert left == []


def test_a_hanging_chat_platform_does_not_hold_up_the_next(monkeypatch):
    fanvue_ran = threading.Event()
    stacked_saw: list[bool] = []

    def run_platform(source) -> None:
        if source.platform == ChatPlatform.STACKED:
            stacked_saw.append(fanvue_ran.wait(timeout=2))
        else:
            fanvue_ran.set()

    monkeypatch.setattr(chat_history, "_run_platform", run_platform)
    monkeypatch.setattr(chat_history, "enabled", lambda _platform: True)
    chat_sweeps = [s for s in sweeps.sweeps() if s.name.startswith("chat history")]

    _until(stacked_saw, 1, *chat_sweeps)

    assert stacked_saw == [True]


def test_the_web_process_runs_all_fifteen_sweeps():
    assert {sweep.name for sweep in sweeps.sweeps()} == {
        "recovery",
        "reel watch",
        "account tracker",
        "link sync",
        "drive",
        "chat webhooks",
        "chat history stacked",
        "chat history fanvue",
        "chat wait events",
        "chat wait rechecks",
        "daily report",
        "telegram asks",
        "edit cleanup",
        "raw files",
        "preview backfill",
    }


def _account_sweep() -> Sweep:
    return next(s for s in sweeps.sweeps() if s.name == "account tracker")


def test_scheduled_account_scans_run_on_production_only(monkeypatch):
    """Dev runs on a copy of prod's data: its own scans would pay Apify again
    for the same accounts (owner, 2026-11-07)."""
    monkeypatch.delenv("OFM_ACCOUNTS_SCHEDULED_SCANS", raising=False)
    monkeypatch.setenv("OFM_ENV", "development")
    assert not _account_sweep().enabled()
    monkeypatch.setenv("OFM_ENV", "production")
    assert _account_sweep().enabled()


def test_scheduled_account_scans_can_be_switched(monkeypatch):
    monkeypatch.setenv("OFM_ENV", "development")
    monkeypatch.setenv("OFM_ACCOUNTS_SCHEDULED_SCANS", "true")
    assert _account_sweep().enabled()
    monkeypatch.setenv("OFM_ENV", "production")
    monkeypatch.setenv("OFM_ACCOUNTS_SCHEDULED_SCANS", "false")
    assert not _account_sweep().enabled()
