"""Background-job history: what every generation/download tool writes."""

import time
import uuid
from typing import Any

from sqlalchemy import (
    ColumnElement,
    Text,
    cast,
    delete,
    func,
    or_,
    select,
    type_coerce,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB, aggregate_order_by, array_agg

from ofmhelpers.config import settings
from ofmhelpers.db.models import JobRow
from ofmhelpers.db.repositories.cached_repository import (
    CachedRepository,
    cached,
    invalidates_cache,
)
from ofmhelpers.db.session import session_scope

# Sentinel so update_status can tell "leave this column alone" apart from
# "set it to None" (a failed job explicitly clears result, etc.).
_UNSET = sentinel("_UNSET")


def _job_to_dict(row: JobRow) -> dict:
    return {
        "id": row.id,
        "task": row.task,
        "params": row.params or {},
        "actor": row.actor,
        "status": row.status,
        "result": row.result,
        "error": row.error,
        "created_at": row.created_at,
        "finished_at": row.finished_at,
        "preview": row.preview,
        "hidden_at": row.hidden_at,
    }


class JobRepository(CachedRepository):
    cache_namespace = "job"

    @invalidates_cache
    def create(
        self,
        task_name: str,
        params: dict,
        actor: str | None = None,
        status: str = "running",
    ) -> str:
        job_id = str(uuid.uuid4())[:8]
        with session_scope() as s:
            s.add(
                JobRow(
                    id=job_id,
                    task=task_name,
                    params=params or {},
                    actor=actor,
                    status=status,
                    result=None,
                    error=None,
                    created_at=time.time(),
                    preview=None,
                )
            )
            s.flush()
            self._enforce_cap(s, task_name, actor)
        return job_id

    def _enforce_cap(self, session, task: str, actor: str | None) -> None:
        """Keep each person's history of each task from growing forever: past
        settings.web.max_jobs, their oldest finished (done/failed) rows of
        that task go. Never another person's rows and never a queued or
        running job: a queued run whose row went would still pay, with nobody
        to see or review it, and one cap over the whole table let sign-in
        events push out tool jobs."""
        cap = settings.web.max_jobs
        owner = JobRow.actor.is_(None) if actor is None else JobRow.actor == actor
        stale_ids = (
            session.execute(
                select(JobRow.id)
                .where(
                    JobRow.task == task,
                    owner,
                    JobRow.status.in_(("done", "failed")),
                )
                .order_by(JobRow.created_at.desc())
                .offset(cap)
            )
            .scalars()
            .all()
        )
        if stale_ids:
            session.execute(delete(JobRow).where(JobRow.id.in_(stale_ids)))

    @cached
    def get(self, job_id: str) -> dict | None:
        with session_scope() as s:
            row = s.get(JobRow, job_id)
            return _job_to_dict(row) if row is not None else None

    @invalidates_cache
    def update_status(
        self,
        job_id: str,
        status: str,
        *,
        result: Any = _UNSET,
        error: str | _UNSET | None = _UNSET,
    ) -> None:
        finished = status in ("done", "failed")
        values: dict[str, Any] = {
            "status": status,
            "finished_at": time.time() if finished else None,
        }
        if finished:
            # Only a running job's card reads it.
            values["preview"] = None
        if result is not _UNSET:
            values["result"] = result
        if error is not _UNSET:
            values["error"] = error
        with session_scope() as s:
            s.execute(update(JobRow).where(JobRow.id == job_id).values(**values))

    @invalidates_cache
    def set_preview(self, job_id: str, preview: dict[str, Any]) -> None:
        # UPDATE on a missing id touches 0 rows -- same no-op the old
        # set_job_preview did when the job wasn't found.
        with session_scope() as s:
            s.execute(update(JobRow).where(JobRow.id == job_id).values(preview=preview))

    @invalidates_cache
    def update_result(self, job_id: str, result: Any) -> None:
        with session_scope() as s:
            s.execute(update(JobRow).where(JobRow.id == job_id).values(result=result))

    @invalidates_cache
    def set_hidden(self, job_id: str, hidden_at: float | None) -> None:
        """Hides the job from its galleries (`hidden_at` = when), or shows it
        again (None)."""
        with session_scope() as s:
            s.execute(
                update(JobRow).where(JobRow.id == job_id).values(hidden_at=hidden_at)
            )

    @invalidates_cache
    def delete(self, job_id: str) -> None:
        with session_scope() as s:
            s.execute(delete(JobRow).where(JobRow.id == job_id))

    def list_running(self) -> list[dict]:
        """Jobs still marked running, oldest first. Uncached: recovery
        decides from it which were cut off."""
        with session_scope() as s:
            rows = (
                s.execute(
                    select(JobRow)
                    .where(JobRow.status == "running")
                    .order_by(JobRow.created_at)
                )
                .scalars()
                .all()
            )
            return [_job_to_dict(r) for r in rows]

    def files_naming(self, fragment: str) -> list[Any]:
        """The params and result of every job (any status) whose params or
        result hold `fragment` anywhere. Uncached: the raw-file sweep keeps
        what they name."""
        with session_scope() as s:
            rows = s.execute(
                select(JobRow.params, JobRow.result).where(
                    or_(
                        cast(JobRow.params, Text).contains(fragment, autoescape=True),
                        cast(JobRow.result, Text).contains(fragment, autoescape=True),
                    )
                )
            ).all()
            return [value for row in rows for value in row if value]

    def params_naming(self, fragment: str, while_running: set[str]) -> list[dict]:
        """The params of every job whose params hold `fragment` anywhere:
        whatever its status, but a job of `while_running` only while it is
        running (or queued). Uncached: the /edit sweep keeps the files they
        name."""
        with session_scope() as s:
            rows = (
                s.execute(
                    select(JobRow.params).where(
                        cast(JobRow.params, Text).contains(fragment, autoescape=True),
                        or_(
                            JobRow.task.not_in(while_running),
                            JobRow.status == "running",
                        ),
                    )
                )
                .scalars()
                .all()
            )
            return [params or {} for params in rows]

    @cached
    def list_all(self) -> list[dict]:
        """Newest first."""
        with session_scope() as s:
            rows = (
                s.execute(select(JobRow).order_by(JobRow.created_at.desc()))
                .scalars()
                .all()
            )
            return [_job_to_dict(r) for r in rows]

    @cached
    def counts(self, tasks: tuple[str, ...]) -> dict[str, dict[str, int]]:
        """actor -> task -> how many done jobs, for `tasks` (sorted tuple:
        stable cache key). The admin file manager's per-user totals."""
        with session_scope() as s:
            rows = s.execute(
                select(JobRow.actor, JobRow.task, func.count())
                .where(
                    JobRow.task.in_(tasks),
                    JobRow.actor.is_not(None),
                    JobRow.status == "done",
                )
                .group_by(JobRow.actor, JobRow.task)
            ).all()
        found: dict[str, dict[str, int]] = {}
        for actor, task, n in rows:
            found.setdefault(actor, {})[task] = n
        return found

    @cached
    def list_page(
        self,
        tasks: tuple[str, ...],
        actor: str | None,
        offset: int,
        limit: int,
        status: str | None = None,
        hidden: bool | None = None,
    ) -> dict:
        """One page of the jobs whose task is in `tasks` (and that `actor` ran,
        unless None), newest first, plus how many match in total. Filtered and
        sliced in SQL: a gallery page loads its 20 rows, not the history.
        `hidden`: only the jobs hidden from the galleries (True), only the
        others (False), or both (None).
        `tasks` is a tuple so the cache key is stable -- callers sort it."""
        where: list[ColumnElement[bool]] = [JobRow.task.in_(tasks)]
        if actor is not None:
            where.append(JobRow.actor == actor)
        if status is not None:
            where.append(JobRow.status == status)
        if hidden is not None:
            where.append(
                JobRow.hidden_at.is_not(None) if hidden else JobRow.hidden_at.is_(None)
            )
        with session_scope() as s:
            rows = (
                s.execute(
                    select(JobRow)
                    .where(*where)
                    .order_by(JobRow.created_at.desc())
                    .offset(offset)
                    .limit(limit)
                )
                .scalars()
                .all()
            )
            total = s.execute(select(func.count()).select_from(JobRow).where(*where))
            return {
                "jobs": [_job_to_dict(r) for r in rows],
                "total": total.scalar_one(),
            }

    @cached
    def list_batch(self, task: str, actor: str, batch: str) -> list[dict]:
        """`actor`'s jobs of `task` queued by one submit (params `batch`),
        oldest first: the order they were pasted in."""
        with session_scope() as s:
            rows = (
                s.execute(
                    select(JobRow)
                    .where(
                        JobRow.task == task,
                        JobRow.actor == actor,
                        JobRow.params["batch"].astext == batch,
                    )
                    .order_by(JobRow.created_at)
                )
                .scalars()
                .all()
            )
            return [_job_to_dict(r) for r in rows]

    @invalidates_cache
    def merge_params(self, job_id: str, values: dict[str, Any]) -> None:
        """`values` written into the job's params, the rest kept (JSONB
        `||`). A missing job: nothing happens."""
        with session_scope() as s:
            s.execute(
                update(JobRow)
                .where(JobRow.id == job_id)
                .values(params=JobRow.params.op("||")(type_coerce(values, JSONB)))
            )

    def redone_links(
        self, task: str, actor: str, links: list[str], after: float
    ) -> set[str]:
        """Which of `links` (params `link`) `actor` queued again as `task`
        after `after` and that is still running or done."""
        if not links:
            return set()
        link = JobRow.params["link"].astext
        with session_scope() as s:
            found = s.execute(
                select(link).where(
                    JobRow.task == task,
                    JobRow.actor == actor,
                    JobRow.created_at > after,
                    JobRow.status.in_(("running", "done")),
                    link.in_(links),
                )
            ).scalars()
            return set(found)

    @cached
    def last_param(
        self, tasks: tuple[str, ...], actor: str, field: str, among: tuple[str, ...]
    ) -> str | None:
        """Param `field` of `actor`'s newest job of `tasks` whose value is
        one of `among` (sorted tuples: stable cache key), else None."""
        value = JobRow.params[field].astext
        with session_scope() as s:
            return s.execute(
                select(value)
                .where(JobRow.task.in_(tasks), JobRow.actor == actor, value.in_(among))
                .order_by(JobRow.created_at.desc())
                .limit(1)
            ).scalar()

    @cached
    def last_used_firsts(
        self, tasks: tuple[str, ...], actor: str, field: str, limit: int
    ) -> list[str]:
        """The first file of the list param `field` (`reference_images`: the
        subject) of `actor`'s jobs of `tasks` (sorted tuple: stable cache
        key), last used first, one path per file (`_firsts`)."""
        return self._firsts(
            tasks, actor, field, limit, [func.max(JobRow.created_at).desc()]
        )

    @cached
    def most_used_firsts(
        self, tasks: tuple[str, ...], actor: str, field: str, limit: int
    ) -> list[str]:
        """As `last_used_firsts`, the one in the most jobs first (a tie: the
        last used)."""
        return self._firsts(
            tasks,
            actor,
            field,
            limit,
            [func.count().desc(), func.max(JobRow.created_at).desc()],
        )

    def _firsts(
        self,
        tasks: tuple[str, ...],
        actor: str,
        field: str,
        limit: int,
        order: list[Any],
    ) -> list[str]:
        """A file is its content: the store names it `<sha256>__<name>`, and
        one picture sits under several names and paths (prod 2026-11-06: 44
        paths, 36 pictures), so files group by that hash; each group answers
        the path its newest job used."""
        first = JobRow.params[field][0].astext
        content = func.split_part(func.regexp_replace(first, "^.*/", ""), "__", 1)
        with session_scope() as s:
            return list(
                s.execute(
                    select(
                        array_agg(aggregate_order_by(first, JobRow.created_at.desc()))[
                            1
                        ]
                    )
                    .where(
                        JobRow.task.in_(tasks),
                        JobRow.actor == actor,
                        first.is_not(None),
                    )
                    .group_by(content)
                    .order_by(*order)
                    .limit(limit)
                ).scalars()
            )

    @cached
    def count_hidden(self, tasks: tuple[str, ...], actor: str) -> int:
        """How many of `actor`'s jobs of `tasks` are hidden (sorted tuple:
        stable cache key) -- whether a gallery offers "Show hidden"."""
        with session_scope() as s:
            return s.execute(
                select(func.count())
                .select_from(JobRow)
                .where(
                    JobRow.task.in_(tasks),
                    JobRow.actor == actor,
                    JobRow.hidden_at.is_not(None),
                )
            ).scalar_one()
