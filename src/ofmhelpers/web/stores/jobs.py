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

import time
from functools import lru_cache
from pathlib import Path
from typing import Any

import logfire
from pydantic import BaseModel
from redis.exceptions import RedisError
from rq.timeouts import JobTimeoutException

from ofmhelpers.aigenproviders import tasks
from ofmhelpers.aigenproviders.errors import ProviderError
from ofmhelpers.cache import PREVIEWS_QUEUE, enqueue_to
from ofmhelpers.log import get_logger
from ofmhelpers.web.db.repositories import JobRepository
from ofmhelpers.web.previews import warm_previews
from ofmhelpers.web.schemas import Job, JobPreview, ResultFile
from ofmhelpers.web.stores.users import agency_id

logger = get_logger(__name__)


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
    """`actor`: the id (as a string) of the user who ran it -- whose job it
    is (only they see it), and who the action log names. None (scripts,
    tests): the agency's, like every store call without an owner."""
    if actor is None:
        actor = str(agency_id())
    return _repository().create(task_name, params, actor=actor, status="running")


def log_event(task_name: str, actor: str | None, params: dict | None = None) -> str:
    """For instantaneous audit events (login/logout) rather than background
    work -- recorded straight into the Action log already "done", no
    background task involved. `params`: what the log shows with it (who,
    signed in how)."""
    return _repository().create(task_name, params or {}, actor=actor, status="done")


def resumable(exc: BaseException) -> bool:
    """Whether a job failing with `exc` keeps its provider request(s) for
    recovery to finish: a provider error (timed out, download failed) or RQ
    ending the job at its time limit -- paid pieces of a long clone
    included -- or an error that says so itself (`keep_request`: a /clone
    export that failed every try, re-run a bounded number of times).
    Anything else failed after the result was in (asset store): recovery
    re-running it would fail the same way every sweep."""
    return isinstance(exc, (ProviderError, JobTimeoutException)) or bool(
        getattr(exc, "keep_request", False)
    )


def run_job(job_id: str, fn, kwargs: dict):
    """Call this via queue.enqueue(run_job, job_id, fn, kwargs) (or, until an
    endpoint is migrated, BackgroundTasks.add_task). Writes the terminal
    status transition straight to Postgres so the API process sees it even
    when this runs in a separate worker. One trace per run (telemetry.py):
    the job, who started it, and every call it makes inside.

    A job whose row is gone (the history cap, a heal) is not run: nobody
    could see or review what it made, yet it would pay the provider."""
    row = _repository().get(job_id)
    if row is None:
        logger.warning("job %s no longer exists: not run", job_id)
        tasks.forget(job_id)
        return
    with logfire.span(
        "job {task} {job_id}",
        job_id=job_id,
        task=row["task"],
        actor=row["actor"],
        function=fn.__qualname__,
    ) as span:
        try:
            result = fn(**kwargs)
            _repository().update_status(job_id, "done", result=_stored(result))
            # Saved: its provider request (if any) needs no resuming.
            tasks.forget(job_id)
        except Exception as exc:
            # The status pages show this directly to whoever is running the
            # job -- a raw traceback isn't useful to them, just the
            # exception's own message (e.g. "Wrong API Key"). Full detail
            # goes to the logs and the trace for whoever is debugging it.
            _repository().update_status(
                job_id, "failed", error=str(exc) or exc.__class__.__name__
            )
            if not resumable(exc):
                tasks.forget(job_id)
            span.record_exception(exc)
            span.set_level("error")
            logger.exception("job %s failed", job_id)
            return
    _queue_previews(job_id)


def _queue_previews(job_id: str) -> None:
    """A finished job's gallery previews, made now on the previews worker so
    its first viewer waits for none (see web/previews.py). Redis down: the
    preview routes make them on request instead."""
    job = get_job(job_id)
    if job is None:
        return
    try:
        enqueue_to(PREVIEWS_QUEUE, warm_previews, job.id, job.local_paths())
    except RedisError:
        logger.warning("could not queue previews of job %s", job_id, exc_info=True)


def running_jobs() -> list[Job]:
    """Every job still marked running (recovery: which were cut off)."""
    return [Job.model_validate(row) for row in _repository().list_running()]


def reopen_job(job_id: str) -> None:
    """Back to running, error cleared, for a job about to be run again."""
    _repository().update_status(job_id, "running", error=None)


def fail_job(job_id: str, error: str) -> None:
    _repository().update_status(job_id, "failed", error=error)


def _stored(result: Any) -> Any:
    """A run function's return value as the JSON the result column holds:
    its models (`ResultFile`, `DownloadResult`) dumped, anything else as is."""
    if not isinstance(result, list):
        return result
    return [_stored_entry(item) for item in result]


def _stored_entry(item: Any) -> Any:
    if isinstance(item, ResultFile):
        return item.to_result()
    if isinstance(item, BaseModel):
        return item.model_dump(mode="json")
    return item


def get_job(job_id: str, actor: str | None = None) -> Job | None:
    """The job, or None if it doesn't exist -- or, when `actor` is given, if
    someone else ran it (a session passes its own, AuthMiddleware.actor --
    an admin's too). None is "no restriction": the worker only."""
    row = _repository().get(job_id)
    if row is None or (actor is not None and row["actor"] != actor):
        return None
    return Job.model_validate(row)


def set_job_preview(job_id: str, preview: JobPreview) -> None:
    """Lets a still-running generation job publish an interim, already-usable
    result (a hosted remote_url) before its final local-file `result` is
    ready. job_status_payload only serves this while status is "running" --
    once the job finishes (done or failed) the real `result`/`error` takes
    over and this is simply never read again."""
    _repository().set_preview(job_id, preview.model_dump())


def list_jobs() -> list[Job]:
    """Every job, newest first, self-healed (see `_heal`). Use
    `list_jobs_page` for anything that only renders a page of them: healing
    stats every result file of every job, which is wasted on the ones the
    caller is about to slice off."""
    return _heal([Job.model_validate(row) for row in _repository().list_all()])


def list_finished_jobs() -> list[Job]:
    """Every done job, newest first, exactly as stored: never healed. For a
    background pass over the whole history (web/preview_backfill.py), which
    must not delete jobs because their files are missing -- a volume not
    mounted yet at startup, or a database copied without its files (dev on a
    prod snapshot) would otherwise lose the whole history."""
    return [
        job
        for job in (Job.model_validate(row) for row in _repository().list_all())
        if job.status == "done"
    ]


def list_jobs_page(
    tasks: set[str],
    offset: int,
    limit: int,
    actor: str | None = None,
    status: str | None = None,
    hidden: bool | None = None,
) -> tuple[list[Job], int]:
    """One page of the jobs whose task is in `tasks` (and, when `actor` is
    given, that it ran -- same meaning as get_job's), newest first, plus how
    many there are in total (before the slice, so the caller can tell whether
    another page exists). `hidden`: only the jobs hidden from the galleries
    (`hide_job`; a gallery's "Show hidden"), only the others (a gallery), or
    both (None: the file manager, character sheet, uniqify).

    Filtered and sliced in SQL (`JobRepository.list_page`), and only the page
    is self-healed, so rendering 20 cards costs 20 jobs' worth of rows and
    disk checks instead of the whole history's."""
    page = _repository().list_page(
        tuple(sorted(tasks)), actor, offset, limit, status, hidden
    )
    jobs = [Job.model_validate(row) for row in page["jobs"]]
    return _heal(jobs), page["total"]


def list_batch(task: str, batch: str, actor: str) -> list[Job]:
    """`actor`'s jobs of `task` from one submit (params `batch`), oldest
    first. Not healed: callers read params only."""
    return [
        Job.model_validate(row) for row in _repository().list_batch(task, actor, batch)
    ]


def merge_params(job_id: str, values: dict[str, Any]) -> None:
    """`values` added to the job's params (the rest kept): what a run
    learns that its re-run needs (a /clone-links run's downloaded source).
    Never a key: params are shown on the job's page."""
    _repository().merge_params(job_id, values)


def redone_links(task: str, actor: str, links: list[str], after: float) -> set[str]:
    """Which of `links` `actor` queued again as `task` after `after`
    (created_at), still running or done: a stale failed card's Recreate
    leaves those out."""
    return _repository().redone_links(task, actor, links, after)


def count_hidden_jobs(tasks: set[str], actor: str) -> int:
    """How many of `actor`'s jobs of `tasks` are hidden from the galleries."""
    return _repository().count_hidden(tuple(sorted(tasks)), actor)


def hide_job(job_id: str, actor: str, hidden: bool) -> Job | None:
    """Hides `actor`'s job from its galleries, or shows it again; None when
    it is not theirs (or gone). A view choice only: the job, its files and
    the action log stay as they are."""
    job = get_job(job_id, actor)
    if job is None or hidden == (job.hidden_at is not None):
        return job
    hidden_at = time.time() if hidden else None
    _repository().set_hidden(job_id, hidden_at)
    return job.model_copy(update={"hidden_at": hidden_at})


def job_counts(tasks: set[str]) -> dict[str, dict[str, int]]:
    """actor -> task -> how many done jobs, for `tasks`."""
    return _repository().counts(tuple(sorted(tasks)))


def _heal(jobs: list[Job]) -> list[Job]:
    """Self-heals history when a result file was deleted on disk (through the
    file manager, or by hand on the server): drops just the missing file(s)
    from a job's result, or the whole job if nothing in it still exists, so a
    removed file's gallery card disappears instead of turning into a dead
    link. The healed state is written back to Postgres. Jobs without a file
    result (still running, failed, an audit event) pass through untouched."""
    surviving: list[Job] = []
    for job in jobs:
        if job.status != "done" or not (job.result_files() or job.is_grouped):
            surviving.append(job)
            continue

        kept = _live_entries(job)
        if kept == job.result:
            surviving.append(job)
        elif kept:
            job.result = kept
            _repository().update_result(job.id, kept)
            surviving.append(job)
        else:
            # Nothing of this job still exists on disk -- drop it entirely.
            _repository().delete(job.id)
    return surviving


def _live_entries(job: Job) -> list[dict]:
    """The job's stored result minus whatever no longer exists on disk. A
    kept entry stays exactly as stored: a task's own fields on top of
    `ResultFile`'s (a /clone-links Check's `url`/`seconds`) were lost when
    entries went back through `ResultFile` (2026-10-25)."""
    if not job.is_grouped:
        # A path of None means the local download never finished and this
        # entry only ever lived as a remote_url -- there's no local file to go
        # stale, so it's never pruned.
        entries = job.result if isinstance(job.result, list) else []
        return [
            entry
            for entry, f in zip(entries, job.result_files(), strict=True)
            if f.path is None or Path(f.path).is_file()
        ]

    kept = []
    for entry in job.download_results():
        live = [p for p in entry.output_paths if p.is_file()]
        if live:
            kept.append(entry.model_copy(update={"output_paths": live}))
        elif not entry.success:
            kept.append(entry)  # a failed entry never had a file to lose
    return [e.model_dump(mode="json") for e in kept]
