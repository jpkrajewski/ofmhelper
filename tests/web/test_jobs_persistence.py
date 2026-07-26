"""
Covers web/jobs.py's Postgres persistence (create_job/log_event/run_job write
through to the jobs table, and the record survives a restart because the DB is
the store) and its self-healing prune: a "done" job whose result file(s) have
been deleted (through the file manager, or by hand on the server) should drop
out of list_jobs() instead of leaving a dead link in the /generate gallery or
the Action log.

conftest.py points the app at a throwaway test database and truncates the
tables between tests, so these never touch dev/prod data.
"""

from ofmhelpers.web.db.repository import JobRepository
from ofmhelpers.web.jobs import (
    create_job,
    get_job,
    list_jobs,
    log_event,
    run_job,
)


def test_create_job_persists_immediately():
    job_id = create_job("seedance", {"prompt": "a cat"}, actor="admin")

    job = get_job(job_id)
    assert job["task"] == "seedance"
    assert job["params"] == {"prompt": "a cat"}
    assert job["status"] == "running"
    assert job["actor"] == "admin"


def test_run_job_success_persists_result():
    job_id = create_job("seedance", {})
    run_job(job_id, lambda: [{"name": "out.mp4", "path": "/tmp/out.mp4"}], {})

    job = get_job(job_id)
    assert job["status"] == "done"
    assert job["result"] == [{"name": "out.mp4", "path": "/tmp/out.mp4"}]


def test_run_job_failure_persists_error():
    job_id = create_job("seedance", {})

    def boom():
        msg = "Wrong API Key"
        raise ValueError(msg)

    run_job(job_id, boom, {})

    job = get_job(job_id)
    assert job["status"] == "failed"
    assert job["error"] == "Wrong API Key"


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

    assert any(j["id"] == job_id for j in list_jobs())

    out_file.unlink()  # e.g. deleted through the file manager

    assert not any(j["id"] == job_id for j in list_jobs())
    assert get_job(job_id) is None


def test_list_jobs_keeps_a_job_still_running_even_with_no_result_yet():
    job_id = create_job("seedance", {})  # still "running", result is None

    assert any(j["id"] == job_id for j in list_jobs())


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

    job = next(j for j in list_jobs() if j["id"] == job_id)
    urls = {r["url"] for r in job["result"]}
    assert urls == {"a", "c"}  # "b" had nothing left to show, "c" was a failure


def test_log_event_is_never_pruned_it_has_no_result():
    job_id = log_event("login", actor="admin")

    assert any(j["id"] == job_id for j in list_jobs())


def test_list_jobs_ignores_a_non_list_result():
    """todo_drive_upload's result is a plain Drive file ID string, not a
    list of file dicts like the generator/downloader jobs. Pruning used to
    assume every "done" job's result was a list[dict] and blew up iterating
    over the string's characters (AttributeError: 'str' object has no
    attribute 'get') -- such jobs should just be left alone."""
    job_id = create_job("todo_drive_upload", {"todo_id": "abc"})
    run_job(job_id, lambda: "1AbCdEfGhIjKlMnOp", {})

    job = next(j for j in list_jobs() if j["id"] == job_id)
    assert job["result"] == "1AbCdEfGhIjKlMnOp"
