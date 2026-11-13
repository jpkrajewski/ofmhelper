"""
Job history, now backed by Postgres (see db/) instead of an in-memory
dict mirrored to uploads/jobs.json. The public API here is unchanged --
create_job/log_event/get_job/set_job_preview/list_jobs -- so the
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

Running a job (`run_job`, `start_job`: previews, provider requests, the
trace) is `features/jobs/runner.py`. The `run_job` here is only the name jobs
queued before that move carry in Redis: it hands them to that runner.
"""

import time
from collections.abc import Callable, Collection
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from ofmhelpers.db.repositories import JobRepository
from ofmhelpers.db.stores import downloads
from ofmhelpers.db.stores.users import agency_id, get_user
from ofmhelpers.log import get_logger
from ofmhelpers.schemas import Job, JobPreview, ResultFile
from ofmhelpers.schemas.downloads import RemovedFile
from ofmhelpers.schemas.persistence import (
    Created,
    GalleryJobs,
    JobCount,
    JobHeader,
    JobPage,
    JobParams,
    Named,
    Spend,
    SpendPeriod,
)
lazy from ofmhelpers.schemas.persistence import JobCost, JobMeta

logger = get_logger(__name__)


@lru_cache(maxsize=1)
def _repository() -> JobRepository:
    """The process-wide job repository, built on first use rather than at
    import. Lazy because constructing it binds a Redis connection for its
    cache, and at import time OFM_REDIS_URL may not be its final value yet."""
    return JobRepository()


# Whose jobs a read is for: one person (`AuthMiddleware.actor`, a worker's
# `actor` kwarg) or several (`Viewer.actors`: a person and their VAs), as
# the user ids' text. None is "no restriction": the worker and sweeps only.
type Readers = str | Collection[str] | None


def _user_id(actor: str | None) -> int | None:
    """The user an `actor` names; None for none or for text that is no
    user id (nobody's)."""
    return int(actor) if actor is not None and actor.isdecimal() else None


def _user_ids(actors: Readers) -> tuple[int, ...] | None:
    """`actors` as the repository's sorted ids (a stable cache key); None
    stays "no restriction". Text that is no user id names nobody."""
    if actors is None:
        return None
    named = [actors] if isinstance(actors, str) else actors
    return tuple(sorted({int(a) for a in named if a.isdecimal()}))


def create_job(task_name: str, params: dict, actor: str | None = None) -> str:
    """`actor`: the id (as a string) of the user who ran it -- whose job it
    is (they and their owner see it), and who the action log names. None
    (scripts, tests): the agency's, like every store call without an owner.
    Text that is no user id: nobody's."""
    user_id = agency_id() if actor is None else _user_id(actor)
    job_id = _repository().create(task_name, params, user_id=user_id, status="running")
    # Inside the request's trace: what ties it to the job's own trace.
    logger.info("job %s created: %s", job_id, task_name)
    return job_id


def log_event(task_name: str, actor: str | None, params: dict | None = None) -> str:
    """For instantaneous audit events (login/logout) rather than background
    work -- recorded straight into the Action log already "done", no
    background task involved. `params`: what the log shows with it (who,
    signed in how)."""
    return _repository().create(
        task_name, params or {}, user_id=_user_id(actor), status="done"
    )


# The one that runs jobs, `(job_id, fn, kwargs)`: `features/jobs/runner.py`
# puts itself here when imported (the web app and `worker._preload_jobs`
# import it before any job runs).
type Runner = Callable[[str, Callable[..., Any], dict], None]


@lru_cache(maxsize=1)
def _runners() -> list[Runner]:
    return []


def register_runner(runner: Runner) -> None:
    _runners()[:] = [runner]


def run_job(job_id: str, fn: Callable[..., Any], kwargs: dict) -> None:
    """Not for new code: `features/jobs/runner.py` `run_job` is what gets
    queued. Jobs queued before it moved name this function
    (`ofmhelpers.db.stores.jobs.run_job`); it passes them on."""
    runners = _runners()
    if not runners:
        msg = "no job runner registered: import ofmhelpers.features.jobs.runner"
        raise RuntimeError(msg)
    runners[0](job_id, fn, kwargs)


def task_of(job_id: str) -> JobHeader | None:
    """What the runner's trace names of a job; None when its row is gone."""
    job = _repository().get(job_id)
    return JobHeader(task=job.task, actor=job.actor) if job else None


def finish_job(job_id: str, result: Any) -> None:
    """Done, with what its run function returned as the stored result."""
    _repository().update_status(job_id, "done", result=_stored(result))


def record_failure(job_id: str, error: str) -> None:
    """Failed by its own run, which logs the exception itself."""
    _repository().update_status(job_id, "failed", error=error)


def keep_cost(job_id: str, meta: JobMeta, cost: JobCost) -> None:
    """What happened on the way (`jobs.meta`: its provider requests) and
    what the run cost (`jobs.cost_usd`, `cost_estimated`)."""
    _repository().set_cost(
        job_id, meta.model_dump(mode="json"), cost.usd, cost.estimated
    )


def spend(user_ids: Collection[int], periods: list[SpendPeriod]) -> list[Spend]:
    """What each of `user_ids`' runs cost in each of `periods`, summed in
    SQL: one entry per person and period that has a run."""
    return _repository().spend(tuple(sorted(user_ids)), tuple(periods))


def _stored(result: Any) -> Any:
    """A run function's return value as the JSON the result column holds:
    its models (`ResultFile`, `DownloadResult`, a job's own summary) dumped,
    anything else as is."""
    if isinstance(result, BaseModel):
        return result.model_dump(mode="json")
    if not isinstance(result, list):
        return result
    return [_stored_entry(item) for item in result]


def _stored_entry(item: Any) -> Any:
    if isinstance(item, ResultFile):
        # Only what was set: a field never given stays out of the stored JSON.
        return item.model_dump(mode="json", exclude_unset=True)
    if isinstance(item, BaseModel):
        return item.model_dump(mode="json")
    return item


def running_jobs() -> list[Job]:
    """Every job still marked running (recovery: which were cut off)."""
    return _repository().list_running()


def params_naming(fragment: str, while_running: set[str]) -> list[JobParams]:
    """The params of every job naming `fragment`, a job of `while_running`
    only while it runs or waits (the /edit sweep: what a job names, it
    keeps)."""
    return _repository().params_naming(fragment, while_running)


def files_naming(fragment: str) -> list[Any]:
    """The params and results of every job naming `fragment` (the raw-file
    sweep: a file a job names is kept)."""
    return _repository().files_naming(fragment)


def naming_each(fragments: list[str]) -> list[Named]:
    """Which of `fragments` (a file's digest, a name) a job names, each with
    its newest use (the downloads sweep: a video a clone used is kept
    longer, one a running job reads is never removed)."""
    return _repository().naming_each(tuple(sorted(set(fragments))))


def reopen_settled(job_id: str) -> bool:
    """A done or failed job back to running, to be run again on its
    person's word; False when it is running already: a second click
    queues nothing."""
    return _repository().reopen_settled(job_id)


def reopen_job(job_id: str) -> None:
    """Back to running, error cleared, for a job about to be run again."""
    _repository().update_status(job_id, "running", error=None)


def drop_job(job_id: str, reason: str) -> None:
    """A job taken out of its queue before it ran (a newer one replaced it):
    settled as failed with `reason`, logged at info -- nothing went wrong."""
    logger.info("job %s dropped: %s", job_id, reason)
    _repository().update_status(job_id, "failed", error=reason)


def fail_job(job_id: str, error: str) -> None:
    logger.error("job %s failed: %s", job_id, error)
    _repository().update_status(job_id, "failed", error=error)


def get_job(job_id: str, actor: Readers = None) -> Job | None:
    """The job, or None if it doesn't exist -- or, when `actor` is given, if
    none of them ran it: a session changing a job passes its own
    (AuthMiddleware.actor -- an admin's too), a session reading one passes
    everyone whose jobs it sees (`Viewer.actors`: its own and its VAs').
    A job with no owner is nobody's. None is "no restriction": the worker
    only."""
    job = _repository().get(job_id)
    readers = _user_ids(actor)
    if job is None or (readers is not None and job.user_id not in readers):
        return None
    return job


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
    return _repository().list_all()


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
    actor: Readers = None,
    status: str | None = None,
    hidden: bool | None = None,
    by_use: bool = False,
    created: Created | None = None,
) -> JobPage:
    """One page of the jobs whose task is in `tasks` (and, when `actor` is
    given, that one of them ran -- same meaning as get_job's), newest first, plus how
    many there are in total (before the slice, so the caller can tell whether
    another page exists). `hidden`: only the jobs hidden from the galleries
    (`hide_job`; a gallery's "Show hidden"), only the others (a gallery), or
    both (None: the file manager, character sheet, uniqify).

    Filtered and sliced in SQL (`JobRepository.list_page`), and only the page
    is checked against the disk (`_visible`), so rendering 20 cards costs 20
    jobs' worth of rows and disk checks instead of the whole history's. A
    page never writes: a job whose files are missing is left out of it, not
    deleted. `by_use`: the job whose download was asked for or used last
    first (/download-assets), not the newest. `created`: only the jobs
    started in that span (a gallery's date filter)."""
    span = created or Created()
    if by_use:
        page = _repository().page_by_use(
            tuple(sorted(tasks)),
            _user_ids(actor),
            offset,
            limit,
            hidden,
            span.since,
            span.until,
        )
    else:
        page = _repository().list_page(
            tuple(sorted(tasks)),
            _user_ids(actor),
            offset,
            limit,
            status,
            hidden,
            span.since,
            span.until,
        )
    return JobPage(jobs=_visible(page.jobs), total=page.total)


def gallery_page(
    tasks: set[str],
    offset: int,
    limit: int,
    actor: Readers,
    hidden: bool | None,
    reader: int | None = None,
    by_use: bool = False,
    created: Created | None = None,
) -> GalleryJobs:
    """One page of a gallery (`list_jobs_page`) and the next page's offset
    (None after the last). `reader`: who looks at it -- a job someone else
    made (their VA) names its maker (`Job.made_by`). `created`: only the
    jobs started in that span."""
    page = list_jobs_page(
        tasks, offset, limit, actor, hidden=hidden, by_use=by_use, created=created
    )
    next_offset = offset + limit
    return GalleryJobs(
        jobs=[_signed(job, reader) for job in page.jobs],
        next_offset=next_offset if next_offset < page.total else None,
    )


def _signed(job: Job, reader: int | None) -> Job:
    """`job` with its maker's name when that is not `reader` (None: nobody
    is named)."""
    if reader is None or job.user_id is None or job.user_id == reader:
        return job
    maker = get_user(job.user_id)
    return job.model_copy(update={"made_by": maker.label}) if maker else job


def list_batch(task: str, batch: str, actor: str) -> list[Job]:
    """`actor`'s jobs of `task` from one submit (params `batch`), oldest
    first. Not healed: callers read params only."""
    user_id = _user_id(actor)
    return [] if user_id is None else _repository().list_batch(task, user_id, batch)


def merge_params(job_id: str, values: dict[str, Any]) -> None:
    """`values` added to the job's params (the rest kept): what a run
    learns that its re-run needs (a /clone-links run's downloaded source).
    Never a key: params are shown on the job's page."""
    _repository().merge_params(job_id, values)


def redone_links(task: str, actor: str, links: list[str], after: float) -> set[str]:
    """Which of `links` `actor` queued again as `task` after `after`
    (created_at), still running or done: a stale failed card's Recreate
    leaves those out."""
    user_id = _user_id(actor)
    if user_id is None:
        return set()
    return _repository().redone_links(task, user_id, links, after)


class ParamHistory(BaseModel, frozen=True):
    """One param (`field`) across `actor`'s jobs of `tasks`."""

    tasks: frozenset[str]
    actor: str
    field: str

    @property
    def _tasks(self) -> tuple[str, ...]:
        """Sorted: the repository's stable cache key."""
        return tuple(sorted(self.tasks))

    @property
    def _user_id(self) -> int | None:
        return _user_id(self.actor)

    def last(self, among: list[str]) -> str | None:
        """The param of the newest job with one of the values `among` (a
        provider the page offers), else None."""
        if self._user_id is None:
            return None
        return _repository().last_param(
            self._tasks, self._user_id, self.field, tuple(sorted(among))
        )

    def last_used_firsts(self, limit: int) -> list[str]:
        """The first file of the list param (the subject of
        `reference_images`), last used first, one path per picture (same
        content = same file). The path may be gone since: the caller
        checks."""
        if self._user_id is None:
            return []
        return _repository().last_used_firsts(
            self._tasks, self._user_id, self.field, limit
        )

    def most_used_firsts(self, limit: int) -> list[str]:
        """As `last_used_firsts`, the one in the most jobs first."""
        if self._user_id is None:
            return []
        return _repository().most_used_firsts(
            self._tasks, self._user_id, self.field, limit
        )


def count_hidden_jobs(tasks: set[str], actor: str | Collection[str]) -> int:
    """How many of `actor`'s jobs (one person's, or several's) of `tasks`
    are hidden from the galleries."""
    return _repository().count_hidden(tuple(sorted(tasks)), _user_ids(actor) or ())


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


def job_counts(tasks: set[str]) -> list[JobCount]:
    """How many done jobs each actor has of each of `tasks`."""
    return _repository().counts(tuple(sorted(tasks)))


def _removed(jobs: list[Job]) -> list[RemovedFile]:
    """The result files of `jobs` the downloads sweep removed on purpose
    (`downloads.stored` false): each keeps its place in the result, so its
    card stays and no file index moves."""
    grouped = [job.id for job in jobs if job.status == "done" and job.is_grouped]
    return downloads.removed_in(grouped)


def _without_missing(job: Job, removed: list[RemovedFile]) -> Job | None:
    """`job` minus the result files no longer on disk (deleted through the
    file manager, or by hand on the server); None when nothing of its result
    is left. A job without a file result (still running, failed, an audit
    event) comes back as it is. A file in `removed` is not missing."""
    if job.status != "done" or not (job.result_files() or job.is_grouped):
        return job
    kept = _live_entries(job, removed)
    if kept == job.result:
        return job
    return job.model_copy(update={"result": kept}) if kept else None


def _visible(jobs: list[Job]) -> list[Job]:
    """`jobs` as a page shows them: a removed file's card loses that file, or
    disappears when none is left, instead of turning into a dead link -- in
    memory only. A read never writes: files that are merely unreachable (a
    volume not mounted yet, dev on a prod snapshot) must not cost the
    history (2026-10-21/24: page views on dev deleted 277, then 392 jobs)."""
    removed = _removed(jobs)
    return [
        shown for job in jobs if (shown := _without_missing(job, removed)) is not None
    ]


def _heal(jobs: list[Job]) -> list[Job]:
    """`_visible`, written back to Postgres: missing files are dropped from
    each job's stored result, and a job with none left is deleted. Only
    `list_jobs()` heals."""
    surviving: list[Job] = []
    removed = _removed(jobs)
    for job in jobs:
        healed = _without_missing(job, removed)
        if healed is None:
            _repository().delete(job.id)
        elif healed is not job:
            _repository().update_result(job.id, healed.result)
            surviving.append(healed)
        else:
            surviving.append(job)
    return surviving


def _live_entries(job: Job, removed: list[RemovedFile]) -> list[dict]:
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
    position = 0
    for entry in job.download_results():
        live = []
        for path in entry.output_paths:
            gone_on_purpose = RemovedFile(job_id=job.id, position=position) in removed
            if gone_on_purpose or path.is_file():
                live.append(path)
            position += 1
        if live:
            kept.append(entry.model_copy(update={"output_paths": live}))
        elif not entry.success:
            kept.append(entry)  # a failed entry never had a file to lose
    return [e.model_dump(mode="json") for e in kept]
