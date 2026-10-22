"""/clone's time estimate (web/clone_eta.py), on prod-shaped numbers:
Higgsfield 720P waits ~55 s per second of video, hung runs 100+ s/s."""

import os

from ofmhelpers.web.clone_eta import clone_eta, clone_rates, took_s
from ofmhelpers.web.schemas import Job


def _run(job_id, rate, duration=10, status="done", provider="higgsfield", res="720P"):
    return Job(
        id=job_id,
        task="clone",
        status=status,
        created_at=1000.0,
        finished_at=1000.0 + rate * duration if status == "done" else None,
        params={"provider": provider, "resolution": res, "duration": duration},
    )


def test_estimate_is_the_median_rate_times_the_clip():
    rates = clone_rates([_run("a", 50), _run("b", 55), _run("c", 60)], runs=30)
    eta = clone_eta(
        rates, {"provider": "higgsfield", "resolution": "720P", "duration": 9}
    )
    assert eta is not None
    assert (eta.eta_s, eta.slow_s) == (495, 540)


def test_a_hung_run_moves_neither_the_estimate_nor_the_slow_mark():
    runs = [_run("a", 50), _run("b", 55), _run("c", 60), _run("hung", 138)]
    [rate] = clone_rates(runs, runs=30).values()
    assert rate.median == 55
    assert rate.worst == 60


def test_only_the_newest_runs_count():
    # Newest first, as the job store returns them.
    runs = [_run("new1", 50), _run("new2", 50), _run("old", 90)]
    [rate] = clone_rates(runs, runs=2).values()
    assert rate.worst == 50


def test_running_failed_and_other_groups_are_not_history():
    runs = [
        _run("r", 0, status="running"),
        Job(
            id="f",
            task="clone",
            status="failed",
            created_at=0.0,
            finished_at=9.0,
            params={"provider": "higgsfield", "resolution": "720P", "duration": 5},
        ),
        _run("k", 40, provider="kie"),
    ]
    rates = clone_rates(runs, runs=30)
    assert (
        clone_eta(
            rates, {"provider": "higgsfield", "resolution": "720P", "duration": 5}
        )
        is None
    )
    assert (
        clone_eta(rates, {"provider": "kie", "resolution": "1080P", "duration": 5})
        is None
    )
    assert (
        clone_eta(rates, {"provider": "kie", "resolution": "720P", "duration": 5})
        is not None
    )


def test_an_old_run_is_timed_by_when_its_result_was_written(tmp_path):
    out = tmp_path / "out.mp4"
    out.write_bytes(b"v")
    os.utime(out, (1500.0, 1500.0))
    job = Job(
        id="o",
        task="clone",
        status="done",
        created_at=1000.0,
        result=[{"name": "out.mp4", "path": str(out)}],
    )
    assert took_s(job) == 500.0

    out.unlink()
    assert took_s(job) is None
