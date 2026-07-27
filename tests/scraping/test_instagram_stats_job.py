"""
The nightly sweep keeps itself alive by re-queueing its own next run, so the
thing worth pinning is the guard: exactly one pending sweep at a time, or the
chain forks and every future midnight runs N sweeps in parallel.
"""

from datetime import UTC, datetime
from types import SimpleNamespace

from ofmhelpers.scraping import instagram_stats_job as job


def _fake_queue(pending_func_names, scheduled, queued_func_names=()):
    jobs = {
        f"id{i}": SimpleNamespace(func_name=name)
        for i, name in enumerate(pending_func_names)
    }
    queued = {
        f"q{i}": SimpleNamespace(func_name=name)
        for i, name in enumerate(queued_func_names)
    }
    both = {**jobs, **queued}
    return SimpleNamespace(
        fetch_job=both.get,
        _ids=list(jobs),
        get_job_ids=lambda: list(queued),
        enqueue_at=lambda when, func: scheduled.append((when, func)),
    )


def _patch_queue(monkeypatch, queue):
    monkeypatch.setattr("ofmhelpers.web.queue.get_queue", lambda: queue)
    monkeypatch.setattr(
        "rq.registry.ScheduledJobRegistry",
        lambda queue: SimpleNamespace(get_job_ids=lambda: queue._ids),
    )
    monkeypatch.setattr(
        "rq.registry.StartedJobRegistry",
        lambda queue: SimpleNamespace(get_job_ids=list),
    )


def test_next_sweep_time_is_the_next_day_at_the_configured_hour(monkeypatch):
    when = job._next_sweep_time_utc()
    assert (when.hour, when.minute, when.second, when.microsecond) == (0, 0, 0, 0)
    assert when > datetime.now(UTC)

    monkeypatch.setenv("OFM_IG_STATS_SWEEP_HOUR_UTC", "4")
    assert job._next_sweep_time_utc().hour == 4


def test_ensure_scheduled_queues_a_sweep_when_none_is_pending(monkeypatch):
    scheduled = []
    _patch_queue(monkeypatch, _fake_queue([], scheduled))

    job.ensure_scheduled()

    assert len(scheduled) == 1
    when, func = scheduled[0]
    assert func is job.collect_all_instagram_stats
    assert when.hour == 0


def test_ensure_scheduled_is_a_noop_when_a_sweep_is_already_pending(monkeypatch):
    scheduled = []
    _patch_queue(
        monkeypatch,
        _fake_queue(
            ["ofmhelpers.scraping.instagram_stats_job.collect_all_instagram_stats"],
            scheduled,
        ),
    )

    job.ensure_scheduled()

    assert scheduled == [], "must not stack a second pending sweep"


def test_ensure_scheduled_ignores_unrelated_scheduled_jobs(monkeypatch):
    scheduled = []
    _patch_queue(monkeypatch, _fake_queue(["some.other.module.do_thing"], scheduled))

    job.ensure_scheduled()

    assert len(scheduled) == 1


def test_active_sweep_id_finds_a_queued_sweep(monkeypatch):
    _patch_queue(
        monkeypatch,
        _fake_queue(
            [],
            [],
            queued_func_names=[
                "ofmhelpers.scraping.instagram_stats_job.collect_all_instagram_stats"
            ],
        ),
    )
    assert job.active_sweep_id() == "q0"


def test_active_sweep_id_is_none_when_only_unrelated_jobs_are_queued(monkeypatch):
    _patch_queue(monkeypatch, _fake_queue([], [], queued_func_names=["some.other.job"]))
    assert job.active_sweep_id() is None
