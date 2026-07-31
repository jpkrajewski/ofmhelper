"""
Job history, now backed by Postgres (see web/db/) instead of an in-memory
dict mirrored to uploads/jobs.json. The public API here is unchanged --
create_job/log_event/run_job/get_job/set_job_preview/list_jobs -- so the
routers and templates that use it are untouched; only the storage underneath
moved to the database.

Why the move: the old in-memory JOBS dict was invisible to any other process,
so once generation runs in a separate RQ worker container the API could no
longer see a job's status. Postgres is the shared source of truth both the API
and the worker read/write. It also removes the lock-free read-modify-write
race the JSON file documented -- each status transition is now a single atomic
UPDATE (see JobRepository.update_status).

Result files still live inside a job's `result` payload exactly as before (a
list of {"name","path"} dicts, a grouped {"url","success","output_paths"}
list, or a bare id string) -- there is no separate file-reference table.
"""

from __future__ import annotations

import traceback
from functools import lru_cache
from pathlib import Path

from ofmhelpers.web.db.repositories import JobRepository


@lru_cache(maxsize=1)
def _repository() -> JobRepository:
    """The process-wide job repository, built on first use rather than at
    import. Lazy because constructing it binds a Redis connection for its
    cache, and at import time OFM_REDIS_URL may not be its final value yet."""
    return JobRepository()


def load_jobs() -> None:
    """No-op kept for main.py's lifespan call. Job history now lives in
    Postgres, so there is nothing to reload into memory at startup -- the
    database *is* the persistent store that survives a restart."""
    return


def create_job(task_name: str, params: dict, actor: str | None = None) -> str:
    """actor is the logged-in role ("admin" / "va") -- recorded so the Action
    log can show who ran what."""
    return _repository().create(task_name, params, actor=actor, status="running")


def log_event(task_name: str, actor: str | None) -> str:
    """For instantaneous audit events (login/logout) rather than background
    work -- recorded straight into the Action log already "done", no
    background task involved."""
    return _repository().create(task_name, {}, actor=actor, status="done")


def run_job(job_id: str, fn, kwargs: dict):
    """Call this via queue.enqueue(run_job, job_id, fn, kwargs) (or, until an
    endpoint is migrated, BackgroundTasks.add_task). Writes the terminal
    status transition straight to Postgres so the API process sees it even
    when this runs in a separate worker."""
    try:
        result = fn(**kwargs)
        _repository().update_status(job_id, "done", result=result)
    except Exception as exc:
        # The status pages show this directly to whoever is running the job --
        # a raw traceback isn't useful to them, just the exception's own
        # message (e.g. "Wrong API Key"). Full detail still goes to the
        # server's stdout/logs for whoever's actually debugging it.
        _repository().update_status(
            job_id, "failed", error=str(exc) or exc.__class__.__name__
        )
        traceback.print_exc()


def get_job(job_id: str) -> dict | None:
    return _repository().get(job_id)


def set_job_preview(job_id: str, preview: dict) -> None:
    """Lets a still-running generation job publish an interim, already-usable
    result (a hosted remote_url) before its final local-file `result` is
    ready. job_status_payload only serves this while status is "running" --
    once the job finishes (done or failed) the real `result`/`error` takes
    over and this is simply never read again."""
    _repository().set_preview(job_id, preview)


def _result_matches_files(result: list[dict]) -> list[dict]:
    """Filters one job's result down to entries whose file(s) still exist on
    disk. Handles both shapes a job's "result" list comes in:
    - flat, one file per entry: {"name", "path"}  (seedance/kling3/
      nanobanana/fake_ai/clean_images/elevenlabs/radio_comms/scraper)
    - grouped by source URL: {"url", "success", "output_paths": [...]}
      (download_images/download_videos)
    """
    if "path" in result[0]:
        # A path of None means the local download never finished and this
        # entry only ever lived as a remote_url (see job_status_payload) --
        # there's no local file to go stale, so it's never pruned.
        return [f for f in result if f["path"] is None or Path(f["path"]).is_file()]

    kept = []
    for entry in result:
        live = [p for p in entry.get("output_paths", []) if Path(p).is_file()]
        if live:
            kept.append({**entry, "output_paths": live})
        elif not entry.get("success"):
            kept.append(entry)  # a failed entry never had a file to lose
    return kept


def list_jobs() -> list[dict]:
    """Every job, newest first, self-healed (see `_heal`). Use
    `list_jobs_page` for anything that only renders a page of them: healing
    stats every result file of every job, which is wasted on the ones the
    caller is about to slice off."""
    return _heal(_repository().list_all())


def list_jobs_page(tasks: set[str], offset: int, limit: int) -> tuple[list[dict], int]:
    """One page of the jobs whose task is in `tasks`, newest first, plus how
    many there are in total (before the slice, so the caller can tell whether
    another page exists).

    Only the page is self-healed: the filter and the slice run off the cached
    repository read, so rendering 20 cards costs 20 jobs' worth of disk checks
    instead of the whole history's."""
    matching = [job for job in _repository().list_all() if job["task"] in tasks]
    return _heal(matching[offset : offset + limit]), len(matching)


def _heal(jobs: list[dict]) -> list[dict]:
    """Self-heals history when a result file was deleted on disk (through the
    file manager, or by hand on the server): drops just the missing file(s)
    from a job's result, or the whole job if nothing in it still exists, so a
    removed file's gallery card disappears instead of turning into a dead
    link. The healed state is written back to Postgres."""
    surviving: list[dict] = []
    for job in jobs:
        result = job.get("result")
        # Not every job's result is a list of file dicts -- e.g. a still-
        # running job (result None) or todo_drive_upload (a plain Drive file
        # ID string). Only the generator/downloader jobs produce the shapes
        # _result_matches_files understands; leave anything else untouched.
        if (
            job.get("status") != "done"
            or not result
            or not isinstance(result, list)
            or not isinstance(result[0], dict)
        ):
            surviving.append(job)
            continue

        kept = _result_matches_files(result)
        if kept == result:
            surviving.append(job)
        elif kept:
            job["result"] = kept
            _repository().update_result(job["id"], kept)
            surviving.append(job)
        else:
            # Nothing of this job still exists on disk -- drop it entirely.
            _repository().delete(job["id"])
    return surviving
