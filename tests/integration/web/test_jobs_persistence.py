"""
Covers web/stores/jobs.py's Postgres persistence (create_job/log_event/run_job write
through to the jobs table, and the record survives a restart because the DB is
the store) and its self-healing prune: a "done" job whose result file(s) have
been deleted (through the file manager, or by hand on the server) should drop
out of list_jobs() instead of leaving a dead link in the /generate gallery or
the Action log.

conftest.py points the app at a throwaway test database and truncates the
tables between tests, so these never touch dev/prod data.
"""

from pathlib import Path

from ofmhelpers.web.db.repositories import JobRepository
from ofmhelpers.web.stores.jobs import (
    create_job,
    get_job,
    list_jobs,
    list_jobs_page,
    log_event,
    reopen_job,
    run_job,
)


def test_create_job_persists_immediately():
    job_id = create_job("seedance", {"prompt": "a cat"}, actor="admin")

    job = get_job(job_id)
    assert job.task == "seedance"
    assert job.params == {"prompt": "a cat"}
    assert job.status == "running"
    assert job.actor == "admin"


def test_run_job_success_persists_result():
    job_id = create_job("seedance", {})
    run_job(job_id, lambda: [{"name": "out.mp4", "path": "/tmp/out.mp4"}], {})

    job = get_job(job_id)
    assert job.status == "done"
    assert job.result == [{"name": "out.mp4", "path": "/tmp/out.mp4"}]


def test_run_job_failure_persists_error():
    job_id = create_job("seedance", {})

    def boom():
        msg = "Wrong API Key"
        raise ValueError(msg)

    run_job(job_id, boom, {})

    job = get_job(job_id)
    assert job.status == "failed"
    assert job.error == "Wrong API Key"


def test_finish_time_is_stamped_on_done_and_failed_and_cleared_on_rerun():
    done = create_job("clone", {})
    assert get_job(done).finished_at is None
    run_job(done, list, {})
    job = get_job(done)
    assert job.finished_at is not None
    assert job.finished_at >= job.created_at

    failed = create_job("clone", {})
    run_job(failed, lambda: 1 / 0, {})
    assert get_job(failed).finished_at is not None

    reopen_job(failed)
    assert get_job(failed).finished_at is None


def test_jobs_survive_a_simulated_restart():
    """The original bug this guards: JOBS used to be memory-only, so restarting
    the process wiped it even though the generated files were untouched. Now
    the database is the store -- a brand-new repository instance (what a fresh
    process would use) reads the same row straight back."""
    job_id = create_job("kling3", {"prompt": "hi"}, actor="va")
    run_job(job_id, lambda: [{"name": "a.mp4", "path": "/tmp/a.mp4"}], {})

    restored = JobRepository().get(job_id)
    assert restored is not None
    assert restored["task"] == "kling3"
    assert restored["status"] == "done"
    assert restored["result"] == [{"name": "a.mp4", "path": "/tmp/a.mp4"}]


def test_list_jobs_drops_a_job_whose_flat_result_file_was_deleted(tmp_path):
    out_file = tmp_path / "gen.mp4"
    out_file.write_bytes(b"video")

    job_id = create_job("seedance", {})
    run_job(job_id, lambda: [{"name": "gen.mp4", "path": str(out_file)}], {})

    assert any(j.id == job_id for j in list_jobs())

    out_file.unlink()  # e.g. deleted through the file manager

    assert not any(j.id == job_id for j in list_jobs())
    assert get_job(job_id) is None


def test_list_jobs_keeps_a_job_still_running_even_with_no_result_yet():
    job_id = create_job("seedance", {})  # still "running", result is None

    assert any(j.id == job_id for j in list_jobs())


def test_list_jobs_partially_prunes_a_grouped_result(tmp_path):
    """download_images/download_videos store results grouped by source URL,
    each carrying its own output_paths list -- only the file(s) that are
    actually gone should be dropped, not the whole entry, and a failed
    entry (no file to begin with) must survive untouched."""
    kept_file = tmp_path / "kept.mp4"
    kept_file.write_bytes(b"video")
    gone_file = tmp_path / "gone.mp4"
    gone_file.write_bytes(b"video")
    gone_file.unlink()  # never existed by the time we check

    job_id = create_job("download_videos", {"urls": ["a", "b", "c"]})
    run_job(
        job_id,
        lambda: [
            {"url": "a", "success": True, "output_paths": [str(kept_file)]},
            {"url": "b", "success": True, "output_paths": [str(gone_file)]},
            {"url": "c", "success": False, "output_paths": [], "error": "boom"},
        ],
        {},
    )

    job = next(j for j in list_jobs() if j.id == job_id)
    urls = {r["url"] for r in job.result}
    assert urls == {"a", "c"}  # "b" had nothing left to show, "c" was a failure


def test_log_event_is_never_pruned_it_has_no_result():
    job_id = log_event("login", actor="admin")

    assert any(j.id == job_id for j in list_jobs())


def test_list_jobs_ignores_a_non_list_result():
    """todo_drive_upload's result is a plain Drive file ID string, not a
    list of file dicts like the generator/downloader jobs. Pruning used to
    assume every "done" job's result was a list[dict] and blew up iterating
    over the string's characters (AttributeError: 'str' object has no
    attribute 'get') -- such jobs should just be left alone."""
    job_id = create_job("todo_drive_upload", {"todo_id": "abc"})
    run_job(job_id, lambda: "1AbCdEfGhIjKlMnOp", {})

    job = next(j for j in list_jobs() if j.id == job_id)
    assert job.result == "1AbCdEfGhIjKlMnOp"


def test_list_jobs_page_filters_slices_and_reports_the_total():
    """What the galleries render: one page of the tasks they show, plus how
    many there are so they can tell whether another page exists."""
    ids = [create_job("fake_ai", {"i": i}) for i in range(5)]
    create_job("download_videos", {})  # a task this gallery doesn't show

    page, total = list_jobs_page({"fake_ai"}, 0, 2)
    assert total == 5
    # newest first
    assert [j.id for j in page] == [ids[4], ids[3]]

    page, total = list_jobs_page({"fake_ai"}, 4, 2)
    assert total == 5
    assert [j.id for j in page] == [ids[0]]

    assert list_jobs_page({"fake_ai"}, 99, 2) == ([], 5)


def test_the_heal_keeps_a_tasks_own_result_fields(tmp_path):
    """A result entry with fields beyond `ResultFile`'s (a /clone-links
    Check's `url`/`seconds`) keeps them through a heal -- dropping a missing
    sibling included. Re-dumping entries through `ResultFile` stripped them
    and wrote that back: Generate on such a Check then failed (2026-10-25)."""
    kept = tmp_path / "kept.mp4"
    kept.write_bytes(b"mp4")
    entry = {"name": kept.name, "path": str(kept), "url": "https://x/1", "seconds": 6.3}
    whole = create_job("fake_fetch", {})
    run_job(whole, lambda: [entry], {})
    pruned = create_job("fake_fetch", {})
    gone = {"name": "gone.mp4", "path": str(tmp_path / "gone.mp4"), "seconds": 1.0}
    run_job(pruned, lambda: [entry, gone], {})

    list_jobs()
    list_jobs_page({"fake_fetch"}, 0, 10)

    assert get_job(whole).result == [entry]
    assert get_job(pruned).result == [entry]


def test_list_jobs_page_only_touches_the_disk_for_the_page(tmp_path, monkeypatch):
    """The self-heal stats every result file it is handed. Doing that for the
    whole history to render one page is what made every scroll tick cost more
    than the page it returned."""
    for i in range(4):
        out = tmp_path / f"a{i}.png"
        out.write_bytes(b"png")
        run_job(
            create_job("fake_ai", {}),
            lambda p=out: [{"name": p.name, "path": str(p)}],
            {},
        )

    checked = []
    real_is_file = Path.is_file

    def counting_is_file(self):
        checked.append(self)
        return real_is_file(self)

    monkeypatch.setattr(Path, "is_file", counting_is_file)
    page, total = list_jobs_page({"fake_ai"}, 0, 2)

    assert total == 4
    assert len(page) == 2
    assert len(checked) == 2


def test_list_jobs_page_filters_and_pages_in_sql():
    """A gallery page is one SQL page: only the asked tasks/actor, newest
    first, with the total before slicing."""
    from ofmhelpers.web.stores.jobs import create_job, list_jobs_page

    va = [create_job("wan3", {"i": i}, actor="va") for i in range(3)]
    create_job("wan3", {}, actor="admin")
    create_job("uniqify", {}, actor="va")

    page, total = list_jobs_page({"wan3", "kling3"}, 0, 2, actor="va")
    rest, _ = list_jobs_page({"wan3"}, 2, 2, actor="va")
    assert total == 3
    # Pages don't overlap and cover exactly the VA's wan3 jobs, newest first
    # (compared on created_at: create_job can tie on a coarse clock).
    pages = page + rest
    assert sorted(j.id for j in pages) == sorted(va)
    stamps = [j.created_at for j in pages]
    assert stamps == sorted(stamps, reverse=True)
    assert list_jobs_page({"wan3"}, 0, 10, actor=None)[1] == 4
