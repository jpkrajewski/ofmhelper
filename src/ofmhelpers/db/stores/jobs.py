"""
Job history, now backed by Postgres (see db/) instead of an in-memory
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
from typing import TYPE_CHECKING, Any

import logfire
from pydantic import BaseModel
from redis.exceptions import RedisError
from rq.timeouts import JobTimeoutException

from ofmhelpers.cache import QueueName, enqueue_to
from ofmhelpers.db.repositories import JobRepository
from ofmhelpers.db.stores.users import agency_id
from ofmhelpers.features.previews.render import warm_previews
from ofmhelpers.integrations.ai_providers import tasks
from ofmhelpers.integrations.ai_providers.errors import ProviderError
from ofmhelpers.log import get_logger
from ofmhelpers.schemas import Job, JobPreview, ResultFile

if TYPE_CHECKING:
    from collections.abc import Callable

logger = get_logger(__name__)


@lru_cache(maxsize=1)
def _repository() -> JobRepository:
    """The process-wide job repository, built on first use rather than at
    import. Lazy because constructing it binds a Redis connection for its
    cache, and at import time OFM_REDIS_URL may not be its final value yet."""
    return JobRepository()


def create_job(task_name: str, params: dict, actor: str | None = None) -> str:
    """`actor`: the id (as a string) of the user who ran it -- whose job it
    is (only they see it), and who the action log names. None (scripts,
    tests): the agency's, like every store call without an owner."""
    if actor is None:
        actor = str(agency_id())
    job_id = _repository().create(task_name, params, actor=actor, status="running")
    # Inside the request's trace: what ties it to the job's own trace.
    logger.info("job %s created: %s", job_id, task_name)
    return job_id


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
        **{"enduser.id": row["actor"]},
    ) as span:
        try:
            result = fn(**kwargs)
            _repository().update_status(job_id, "done", result=_stored(result))
            _record_outcome(span, job_id, result)
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


# RQ shows this path on every job; it resolves through `ofmhelpers._moved`.
run_job.__module__ = "ofmhelpers.web.stores.jobs"


def start_job(
    task: str,
    params: dict,
    run: Callable[..., Any],
    kwargs: dict,
    *,
    actor: str | None,
    queue: QueueName | None = None,
    job_timeout: int | None = None,
) -> str:
    """A new job and its run on `queue` (default: the default queue).
    `kwargs`: what `run` gets besides `job_id`; a key goes there, never in
    `params`, which the job's page shows. A queue failure is raised as it
    came and the row stays `running`: recovery settles it as cut off."""
    job_id = create_job(task, params, actor=actor)
    enqueue_to(
        queue or QueueName.DEFAULT,
        run_job,
        job_id,
        run,
        {"job_id": job_id, **kwargs},
        job_timeout=job_timeout,
    )
    return job_id


def _record_outcome(span: logfire.LogfireSpan, job_id: str, result: Any) -> None:
    """A job that ran to its end can still have failed: a download of three
    links stores which ones did, as results, and is `done`. Only the span and
    the log say so, or a trace of a failed download reads as a success."""
    items = result if isinstance(result, list) else []
    failed = [item for item in items if getattr(item, "success", True) is False]
    if not failed:
        return
    span.set_attributes({"results": len(items), "results_failed": len(failed)})
    span.set_level("error" if len(failed) == len(items) else "warning")
    logger.warning(
        "job %s: %d of %d results failed: %s",
        job_id,
        len(failed),
        len(items),
        [getattr(item, "error", None) for item in failed],
    )


def _queue_previews(job_id: str) -> None:
    """A finished job's gallery previews, made now on the previews worker so
    its first viewer waits for none (see features/previews/render.py). Redis down: the
    preview routes make them on request instead."""
    job = get_job(job_id)
    if job is None:
        return
    try:
        enqueue_to(QueueName.PREVIEWS, warm_previews, job.id, job.local_paths())
    except RedisError:
        logger.warning("could not queue previews of job %s", job_id, exc_info=True)


def running_jobs() -> list[Job]:
    """Every job still marked running (recovery: which were cut off)."""
    return [Job.model_validate(row) for row in _repository().list_running()]


def reopen_job(job_id: str) -> None:
    """Back to running, error cleared, for a job about to be run again."""
    _repository().update_status(job_id, "running", error=None)


def fail_job(job_id: str, error: str) -> None:
    logger.error("job %s failed: %s", job_id, error)
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


def history() -> list[Job]:
    """Every job, newest first, exactly as stored: never healed, nothing
    written. What a read of the whole history uses (the action log, the
    preview backfill): a result whose files cannot be seen right now -- a
    volume not mounted yet, a database copied without its files (dev on a
    prod snapshot) -- must never cost the job."""
    return [Job.model_validate(row) for row in _repository().list_all()]


def list_jobs() -> list[Job]:
    """Every job, newest first, self-healed (see `_heal`): destructive, so no
    request path calls it -- pages read through `list_jobs_page` (shown
    without the missing files, nothing written) and the action log through
    `history`."""
    return _heal(history())


def list_finished_jobs() -> list[Job]:
    """Every done job, newest first, exactly as stored (`history`). For a
    background pass over the whole history (features/previews/backfill.py)."""
    return [job for job in history() if job.status == "done"]


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
    is checked against the disk (`_visible`), so rendering 20 cards costs 20
    jobs' worth of rows and disk checks instead of the whole history's. A
    page never writes: a job whose files are missing is left out of it, not
    deleted."""
    page = _repository().list_page(
        tuple(sorted(tasks)), actor, offset, limit, status, hidden
    )
    jobs = [Job.model_validate(row) for row in page["jobs"]]
    return _visible(jobs), page["total"]


def gallery_page(
    tasks: set[str],
    offset: int,
    limit: int,
    actor: str | None,
    hidden: bool | None,
) -> tuple[list[Job], int | None]:
    """One page of a gallery (`list_jobs_page`) and the next page's offset
    (None after the last)."""
    jobs, total = list_jobs_page(tasks, offset, limit, actor, hidden=hidden)
    next_offset = offset + limit
    return jobs, next_offset if next_offset < total else None


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


def _without_missing(job: Job) -> Job | None:
    """`job` minus the result files no longer on disk (deleted through the
    file manager, or by hand on the server); None when nothing of its result
    is left. A job without a file result (still running, failed, an audit
    event) comes back as it is."""
    if job.status != "done" or not (job.result_files() or job.is_grouped):
        return job
    kept = _live_entries(job)
    if kept == job.result:
        return job
    return job.model_copy(update={"result": kept}) if kept else None


def _visible(jobs: list[Job]) -> list[Job]:
    """`jobs` as a page shows them: a removed file's card loses that file, or
    disappears when none is left, instead of turning into a dead link -- in
    memory only. A read never writes: files that are merely unreachable (a
    volume not mounted yet, dev on a prod snapshot) must not cost the
    history (2026-10-21/24: page views on dev deleted 277, then 392 jobs)."""
    return [shown for job in jobs if (shown := _without_missing(job)) is not None]


def _heal(jobs: list[Job]) -> list[Job]:
    """`_visible`, written back to Postgres: missing files are dropped from
    each job's stored result, and a job with none left is deleted. Only
    `list_jobs()` heals."""
    surviving: list[Job] = []
    for job in jobs:
        healed = _without_missing(job)
        if healed is None:
            _repository().delete(job.id)
        elif healed is not job:
            _repository().update_result(job.id, healed.result)
            surviving.append(healed)
        else:
            surviving.append(job)
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
