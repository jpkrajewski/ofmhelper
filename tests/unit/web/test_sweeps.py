import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
lazy from collections.abc import Coroutine

from ofmhelpers.background import sweeps
from ofmhelpers.background.sweeps import Sweep, running
from ofmhelpers.cache import QueueName
from ofmhelpers.config import settings
from ofmhelpers.db.chats.schema import ChatPlatform
from ofmhelpers.features.chatting import sync as chat_history


def _run(scenario: Coroutine) -> None:
    with ThreadPoolExecutor(1) as pool:
        pool.submit(asyncio.run, asyncio.wait_for(scenario, timeout=5)).result()


def _until(seen: list, count: int, *all_sweeps: Sweep) -> None:

    async def scenario() -> None:
        async with running(list(all_sweeps)):
            while len(seen) < count:  # noqa: ASYNC110
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


def test_a_sweep_that_waits_first_runs_only_after_its_interval():
    ran = []
    asked = []

    def interval() -> float:
        asked.append(len(ran))
        return 0

    _until(
        ran,
        1,
        Sweep(
            name="late",
            run=lambda: ran.append("run"),
            interval_s=interval,
            waits_first=True,
        ),
    )

    assert asked[0] == 0


def test_the_worker_check_gives_the_workers_a_round_to_start_after_a_deploy():
    alive = next(s for s in sweeps.sweeps() if s.run is sweeps.report_alive)

    assert alive.waits_first


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


def test_the_sweeps_process_runs_all_twenty_three_sweeps():
    assert {sweep.name for sweep in sweeps.sweeps()} == {
        "recovery",
        "reel watch",
        "account tracker",
        "link sync",
        "distribution",
        "drive",
        "chat webhooks",
        "chat history stacked",
        "chat history fanvue",
        "chat wait events",
        "chat wait rechecks",
        "daily report",
        "telegram asks",
        "edit cleanup",
        "trials cleanup",
        "downloads cleanup",
        "viral cleanup",
        "raw files",
        "phones backup",
        "sentry heartbeat",
        "idempotency keys",
        "va idle",
        "preview backfill",
    }


def _account_sweep() -> Sweep:
    return next(s for s in sweeps.sweeps() if s.name == "account tracker")


def test_scheduled_account_scans_run_on_production_only(monkeypatch):
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


def _downloads_sweep() -> Sweep:
    return next(s for s in sweeps.sweeps() if s.name == "downloads cleanup")


def test_the_downloads_sweep_runs_on_production_only(monkeypatch):
    monkeypatch.delenv("OFM_DOWNLOADS_SWEEP", raising=False)
    monkeypatch.setenv("OFM_ENV", "development")
    assert not _downloads_sweep().enabled()
    monkeypatch.setenv("OFM_ENV", "production")
    assert _downloads_sweep().enabled()


def test_the_downloads_sweep_can_be_switched(monkeypatch):
    monkeypatch.setenv("OFM_ENV", "development")
    monkeypatch.setenv("OFM_DOWNLOADS_SWEEP", "true")
    assert _downloads_sweep().enabled()
    monkeypatch.setenv("OFM_ENV", "production")
    monkeypatch.setenv("OFM_DOWNLOADS_SWEEP", "false")
    assert not _downloads_sweep().enabled()


def test_a_downloaded_video_is_kept_a_day_two_weeks_a_month(monkeypatch):
    for name in ("KEEP_UNUSED_S", "KEEP_CLONED_S", "KEEP_MAX_S", "SWEEP_S"):
        monkeypatch.delenv(f"OFM_DOWNLOADS_{name}", raising=False)
    kept = settings.downloaders

    assert (kept.keep_unused_s, kept.keep_cloned_s, kept.keep_max_s) == (
        24 * 3600,
        14 * 24 * 3600,
        30 * 24 * 3600,
    )
    assert _downloads_sweep().interval_s() == 3600


def test_a_sweep_with_no_queue_runs_here():
    ran = []

    Sweep(name="light", run=lambda: ran.append("run")).round()

    assert ran == ["run"]


def test_the_sweeps_that_walk_the_disk_or_run_ffmpeg_are_the_workers():
    heavy = {s.name for s in sweeps.sweeps() if s.queue is not None}

    assert heavy == {
        "edit cleanup",
        "trials cleanup",
        "downloads cleanup",
        "viral cleanup",
        "raw files",
    }
    assert {s.queue for s in sweeps.sweeps() if s.queue} == {QueueName.PREVIEWS}
