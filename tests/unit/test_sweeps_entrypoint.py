"""ofmhelpers/sweeps.py, the sweeps container's entrypoint: the sweeps run in
a process of their own (never the web app's), and `compose stop` (SIGTERM,
every deploy) ends it cleanly."""

import signal
import subprocess
import sys
import textwrap
from concurrent.futures import ThreadPoolExecutor

from ofmhelpers import sweeps as entrypoint

_CHILD = textwrap.dedent(
    """
    import asyncio
    from ofmhelpers.features.jobs.sweeps import Sweep
    from ofmhelpers.log import configure_logging
    from ofmhelpers.sweeps import run

    configure_logging()
    tick = Sweep(
        name="tick", run=lambda: print("ran", flush=True), interval_s=lambda: 0.05
    )
    asyncio.run(run([tick]))
    print("exited", flush=True)
    """
)


def test_sigterm_stops_the_sweeps_and_the_process_exits_zero():
    child = subprocess.Popen(
        [sys.executable, "-c", _CHILD],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        assert child.stdout is not None
        seen = []
        for line in child.stdout:
            seen.append(line)
            if line.strip() == "ran":
                break
        child.send_signal(signal.SIGTERM)
        rest, _ = child.communicate(timeout=30)
    finally:
        child.kill()
    output = "".join(seen) + rest

    assert child.returncode == 0, output
    assert "sweeps stopped" in output
    assert output.rstrip().endswith("exited")


def test_main_sets_up_logging_and_telemetry_then_runs_every_sweep(monkeypatch):
    calls = []
    monkeypatch.setattr(entrypoint, "configure_telemetry", calls.append)
    monkeypatch.setattr(entrypoint, "configure_logging", lambda: calls.append("log"))
    monkeypatch.setattr(entrypoint, "flush_telemetry", lambda: calls.append("flush"))
    monkeypatch.setattr(entrypoint, "sweeps", lambda: ["every sweep"])

    async def run(all_sweeps: list) -> None:
        calls.append(all_sweeps)

    monkeypatch.setattr(entrypoint, "run", run)

    # In a thread of its own: e2e tests in the same worker leave Playwright's
    # event loop running in this one, and `asyncio.run` refuses to nest.
    with ThreadPoolExecutor(1) as pool:
        pool.submit(entrypoint.main).result(timeout=30)

    assert calls == ["ascend-sweeps", "log", ["every sweep"], "flush"]
