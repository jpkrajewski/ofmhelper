# Copyright (C) 2026 jpkrajewski

import logging
import time
from collections.abc import Callable, Collection
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel, JsonValue

from ofmhelpers.db.downloads import store as downloads
from ofmhelpers.db.downloads.schema import RemovedFile
from ofmhelpers.db.jobs.repository import JobRepository
from ofmhelpers.db.jobs.schema import (
    Created,
    GalleryJobs,
    Job,
    JobCount,
    JobHeader,
    JobPage,
    JobParams,
    JobPreview,
    JobStatus,
    Named,
    RecordedJob,
    ResultFile,
    SessionSpan,
    Spend,
    SpendPeriod,
    UserDay,
)
from ofmhelpers.db.users.store import agency_id, get_user
lazy from ofmhelpers.db.jobs.schema import JobCost, JobMeta

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _repository() -> JobRepository:
    return JobRepository()


type Readers = str | Collection[str] | None


def _user_id(actor: str | None) -> int | None:
    return int(actor) if actor is not None and actor.isdecimal() else None


def _user_ids(actors: Readers) -> list[int] | None:
    if actors is None:
        return None
    named = [actors] if isinstance(actors, str) else actors
    return sorted({int(a) for a in named if a.isdecimal()})


def create_job(
    task_name: str,
    params: dict[str, Any],
    actor: str | None = None,
) -> str:
    user_id = agency_id() if actor is None else _user_id(actor)
    job_id = _repository().create(task_name, params, user_id=user_id, status="running")
    logger.info("job %s created: %s", job_id, task_name)
    return job_id


def log_event(
    task_name: str,
    actor: str | None,
    params: dict[str, Any] | None = None,
    at: float | None = None,
) -> str:
    return _repository().create(
        task_name,
        params or {},
        user_id=_user_id(actor),
        status="done",
        created_at=at,
    )


type Runner = Callable[..., None]


@lru_cache(maxsize=1)
def _runners() -> list[Runner]:
    return []


def register_runner(runner: Runner) -> None:
    _runners()[:] = [runner]


def run_job(job_id: str, fn: Callable[..., Any], kwargs: dict[str, Any]) -> None:
    runners = _runners()
    if not runners:
        msg = "no job runner registered: import ofmhelpers.features.jobs.runner"
        raise RuntimeError(msg)
    runners[0](job_id, fn, kwargs)


def task_of(job_id: str) -> JobHeader | None:
    job = _repository().get(job_id)
    return JobHeader(task=job.task, actor=job.actor) if job else None


def finish_job(
    job_id: str,
    result: JsonValue | BaseModel | list[BaseModel],
) -> None:
    _repository().update_status(job_id, "done", result=_stored(result))


def record_failure(job_id: str, error: str) -> None:
    _repository().update_status(job_id, "failed", error=error)


def keep_cost(job_id: str, meta: JobMeta, cost: JobCost) -> None:
    _repository().set_cost(
        job_id,
        meta.model_dump(mode="json"),
        cost.usd,
        cost.estimated,
    )


def add_recorded(rows: list[RecordedJob]) -> None:
    _repository().add_recorded(rows)


def delete_recorded(prefix: str) -> int:
    return _repository().delete_like(prefix)


def days(
    user_ids: Collection[int],
    from_ts: float,
    to_ts: float,
    zone: str,
    reel_tasks: Collection[str],
) -> list[UserDay]:
    return _repository().days(
        tuple(sorted(user_ids)),
        from_ts,
        to_ts,
        zone,
        tuple(sorted(reel_tasks)),
    )


def sessions(
    user_ids: Collection[int],
    from_ts: float,
    to_ts: float,
) -> list[SessionSpan]:
    return _repository().sessions(tuple(sorted(user_ids)), from_ts, to_ts)


def spend(user_ids: Collection[int], periods: list[SpendPeriod]) -> list[Spend]:
    return _repository().spend(tuple(sorted(user_ids)), tuple(periods))


def _stored(result: JsonValue | BaseModel | list[BaseModel]) -> JsonValue:
    if isinstance(result, BaseModel):
        return result.model_dump(mode="json")
    if not isinstance(result, list):
        return result
    return [_stored_entry(item) for item in result]


def _stored_entry(item: JsonValue | BaseModel) -> JsonValue:
    if isinstance(item, ResultFile):
        return item.model_dump(mode="json", exclude_unset=True)
    if isinstance(item, BaseModel):
        return item.model_dump(mode="json")
    return item


def running_jobs() -> list[Job]:
    return _repository().list_running()


def params_naming(fragment: str, while_running: set[str]) -> list[JobParams]:
    return _repository().params_naming(fragment, while_running)


def files_naming(fragment: str) -> list[Any]:
    return _repository().files_naming(fragment)


def naming_each(fragments: list[str]) -> list[Named]:
    return _repository().naming_each(tuple(sorted(set(fragments))))


def reopen_settled(job_id: str) -> bool:
    return _repository().reopen_settled(job_id)


def reopen_job(job_id: str) -> None:
    _repository().update_status(job_id, "running", error=None)


def drop_job(job_id: str, reason: str) -> None:
    logger.info("job %s dropped: %s", job_id, reason)
    _repository().update_status(job_id, "failed", error=reason)


def fail_job(job_id: str, error: str) -> None:
    logger.error("job %s failed: %s", job_id, error)
    _repository().update_status(job_id, "failed", error=error)


def get_job(job_id: str, actor: Readers = None) -> Job | None:
    job = _repository().get(job_id)
    readers = _user_ids(actor)
    if job is None or (readers is not None and job.user_id not in readers):
        return None
    return job


def set_job_preview(job_id: str, preview: JobPreview) -> None:
    _repository().set_preview(job_id, preview.model_dump())


def history() -> list[Job]:
    return _repository().list_all()


def list_jobs() -> list[Job]:
    return _heal(history())


def list_finished_jobs() -> list[Job]:
    return [job for job in history() if job.status == "done"]


def list_jobs_page(  # noqa: PLR0913, PLR0917
    tasks: set[str],
    offset: int,
    limit: int,
    actor: Readers = None,
    status: str | None = None,
    hidden: bool | None = None,  # noqa: FBT001
    by_use: bool = False,  # noqa: FBT001, FBT002
    created: Created | None = None,
    not_uploaded: bool = False,  # noqa: FBT001, FBT002
) -> JobPage:
    span = created or Created()
    if not_uploaded:
        page = _repository().page_not_uploaded(
            tuple(sorted(tasks)),
            _user_ids(actor),
            offset,
            limit,
            hidden,
            span.since,
            span.until,
        )
    elif by_use:
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


def gallery_page(  # noqa: PLR0913, PLR0917
    tasks: set[str],
    offset: int,
    limit: int,
    actor: Readers,
    hidden: bool | None,  # noqa: FBT001
    reader: int | None = None,
    by_use: bool = False,  # noqa: FBT001, FBT002
    created: Created | None = None,
    not_uploaded: bool = False,  # noqa: FBT001, FBT002
) -> GalleryJobs:
    page = list_jobs_page(
        tasks,
        offset,
        limit,
        actor,
        hidden=hidden,
        by_use=by_use,
        created=created,
        not_uploaded=not_uploaded,
    )
    next_offset = offset + limit
    return GalleryJobs(
        jobs=[_signed(job, reader) for job in page.jobs],
        next_offset=next_offset if next_offset < page.total else None,
    )


def _signed(job: Job, reader: int | None) -> Job:
    if reader is None or job.user_id is None or job.user_id == reader:
        return job
    maker = get_user(job.user_id)
    return job.model_copy(update={"made_by": maker.label}) if maker else job


def list_batch(task: str, batch: str, actor: str) -> list[Job]:
    user_id = _user_id(actor)
    return [] if user_id is None else _repository().list_batch(task, user_id, batch)


def latest_job(task: str, actor: str, status: JobStatus | None = None) -> Job | None:
    user_id = _user_id(actor)
    return None if user_id is None else _repository().latest(task, user_id, status)


def running_with_links(
    tasks: Collection[str],
    actor: str,
    links: list[str],
) -> list[Job]:
    user_id = _user_id(actor)
    if user_id is None:
        return []
    return _repository().running_with_links(tasks, user_id, links)


def merge_params(job_id: str, values: dict[str, Any]) -> None:
    _repository().merge_params(job_id, values)


def redone_links(task: str, actor: str, links: list[str], after: float) -> set[str]:
    user_id = _user_id(actor)
    if user_id is None:
        return set()
    return _repository().redone_links(task, user_id, links, after)


class ParamHistory(BaseModel, frozen=True):
    tasks: frozenset[str]
    actor: str | frozenset[str]
    field: str

    @property
    def _tasks(self) -> list[str]:
        return sorted(self.tasks)

    @property
    def _user_ids(self) -> list[int]:
        return _user_ids(self.actor) or []

    def last(self, among: list[str]) -> str | None:
        if not self._user_ids:
            return None
        return _repository().last_param(
            self._tasks,
            self._user_ids,
            self.field,
            tuple(sorted(among)),
        )

    def last_used_firsts(self, limit: int) -> list[str]:
        if not self._user_ids:
            return []
        return _repository().last_used_firsts(
            self._tasks,
            self._user_ids,
            self.field,
            limit,
        )

    def most_used_firsts(self, limit: int) -> list[str]:
        if not self._user_ids:
            return []
        return _repository().most_used_firsts(
            self._tasks,
            self._user_ids,
            self.field,
            limit,
        )


def count_hidden_jobs(tasks: set[str], actor: str | Collection[str]) -> int:
    return _repository().count_hidden(tuple(sorted(tasks)), _user_ids(actor) or [])


def hide_job(job_id: str, actor: Readers, hidden: bool) -> Job | None:  # noqa: FBT001
    job = get_job(job_id, actor)
    if job is None or hidden == (job.hidden_at is not None):
        return job
    hidden_at = time.time() if hidden else None
    _repository().set_hidden(job_id, hidden_at)
    return job.model_copy(update={"hidden_at": hidden_at})


def job_counts(tasks: set[str]) -> list[JobCount]:
    return _repository().counts(tuple(sorted(tasks)))


def _removed(jobs: list[Job]) -> list[RemovedFile]:
    grouped = [job.id for job in jobs if job.status == "done" and job.is_grouped]
    return downloads.removed_in(grouped)


def _without_missing(job: Job, removed: list[RemovedFile]) -> Job | None:
    if job.status != "done" or not (job.result_files() or job.is_grouped):
        return job
    kept = _live_entries(job, removed)
    if kept == job.result:
        return job
    return job.model_copy(update={"result": kept}) if kept else None


def _visible(jobs: list[Job]) -> list[Job]:
    removed = _removed(jobs)
    return [
        shown for job in jobs if (shown := _without_missing(job, removed)) is not None
    ]


def _heal(jobs: list[Job]) -> list[Job]:
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


def _live_entries(job: Job, removed: list[RemovedFile]) -> list[JsonValue]:
    if not job.is_grouped:
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
            kept.append(entry)
    return [e.model_dump(mode="json") for e in kept]
