"""Background-job history: what every generation/download tool writes."""

import time
import uuid
from typing import Any
lazy from decimal import Decimal

from sqlalchemy import (
    ColumnElement,
    Float,
    Text,
    and_,
    cast,
    column,
    delete,
    func,
    or_,
    select,
    type_coerce,
    update,
    values,
)
from sqlalchemy.dialects.postgresql import JSONB, aggregate_order_by, array_agg

from ofmhelpers.config import settings
from ofmhelpers.db.models import DownloadRow, JobRow
from ofmhelpers.db.repositories.cached_repository import (
    CachedRepository,
    cached,
    invalidates_cache,
)
from ofmhelpers.db.session import session_scope
from ofmhelpers.schemas.persistence import (
    Job,
    JobCount,
    JobPage,
    JobParams,
    Named,
    Spend,
    SpendPeriod,
)

# Sentinel so update_status can tell "leave this column alone" apart from
# "set it to None" (a failed job explicitly clears result, etc.).
_UNSET = sentinel("_UNSET")


def _job(row: JobRow) -> Job:
    return Job.model_validate(
        {
            "id": row.id,
            "task": row.task,
            "params": row.params or {},
            "user_id": row.user_id,
            "status": row.status,
            "result": row.result,
            "error": row.error,
            "created_at": row.created_at,
            "finished_at": row.finished_at,
            "preview": row.preview,
            "hidden_at": row.hidden_at,
            "meta": row.meta or {},
            "cost_usd": row.cost_usd,
            "cost_estimated": row.cost_estimated,
        }
    )


def _page_filter(
    tasks: tuple[str, ...],
    user_ids: tuple[int, ...] | None,
    status: str | None,
    hidden: bool | None,
) -> list[ColumnElement[bool]]:
    where: list[ColumnElement[bool]] = [JobRow.task.in_(tasks)]
    if user_ids is not None:
        where.append(JobRow.user_id.in_(user_ids))
    if status is not None:
        where.append(JobRow.status == status)
    if hidden is not None:
        where.append(
            JobRow.hidden_at.is_not(None) if hidden else JobRow.hidden_at.is_(None)
        )
    return where


def _page(
    where: list[ColumnElement[bool]], order: list[Any], offset: int, limit: int
) -> JobPage:
    with session_scope() as s:
        rows = (
            s.execute(
                select(JobRow)
                .where(*where)
                .order_by(*order)
                .offset(offset)
                .limit(limit)
            )
            .scalars()
            .all()
        )
        total = s.execute(select(func.count()).select_from(JobRow).where(*where))
        return JobPage(jobs=[_job(r) for r in rows], total=total.scalar_one())


class JobRepository(CachedRepository):
    cache_namespace = "job"

    @invalidates_cache
    def create(
        self,
        task_name: str,
        params: dict,
        user_id: int | None = None,
        status: str = "running",
    ) -> str:
        """`user_id`: whose job it is; None = nobody's (a system event)."""
        job_id = str(uuid.uuid4())[:8]
        with session_scope() as s:
            s.add(
                JobRow(
                    id=job_id,
                    task=task_name,
                    params=params or {},
                    user_id=user_id,
                    status=status,
                    result=None,
                    error=None,
                    created_at=time.time(),
                    preview=None,
                )
            )
            s.flush()
            self._enforce_cap(s, task_name, user_id)
        return job_id

    def _enforce_cap(self, session, task: str, user_id: int | None) -> None:
        """Keep each person's history of each task from growing forever: past
        settings.web.max_jobs, their oldest finished (done/failed) rows of
        that task go. Never another person's rows and never a queued or
        running job: a queued run whose row went would still pay, with nobody
        to see or review it, and one cap over the whole table let sign-in
        events push out tool jobs."""
        cap = settings.web.max_jobs
        owner = (
            JobRow.user_id.is_(None) if user_id is None else JobRow.user_id == user_id
        )
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
    def get(self, job_id: str) -> Job | None:
        with session_scope() as s:
            row = s.get(JobRow, job_id)
            return _job(row) if row is not None else None

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
    def reopen_settled(self, job_id: str) -> bool:
        """A done or failed job back to running (error cleared), in one
        UPDATE; False when it is running already (or gone): of two clicks,
        one gets True."""
        with session_scope() as s:
            found = s.execute(
                update(JobRow)
                .where(JobRow.id == job_id, JobRow.status != "running")
                .values(status="running", error=None, finished_at=None)
                .returning(JobRow.id)
            )
            return found.scalar_one_or_none() is not None

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

    def list_running(self) -> list[Job]:
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
            return [_job(r) for r in rows]

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

    def params_naming(self, fragment: str, while_running: set[str]) -> list[JobParams]:
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
            return [JobParams(params=params or {}) for params in rows]

    def naming_each(self, fragments: tuple[str, ...]) -> list[Named]:
        """For each of `fragments` a job names: when the newest job whose
        params hold it was started, whether one of those is running (or
        queued), and whether a job's result holds it. One query for them
        all; a fragment no job names is not in the answer. Uncached: the
        downloads sweep keeps what they name."""
        if not fragments:
            return []
        asked = values(column("fragment", Text), name="asked").data(
            [(fragment,) for fragment in fragments]
        )
        fragment = asked.c.fragment
        in_params = func.strpos(cast(JobRow.params, Text), fragment) > 0
        in_result = (
            func.strpos(func.coalesce(cast(JobRow.result, Text), ""), fragment) > 0
        )
        query = (
            select(
                fragment,
                func.max(JobRow.created_at).filter(in_params),
                func.coalesce(
                    func.bool_or(JobRow.status == "running").filter(in_params), False
                ),
                func.bool_or(in_result),
            )
            .select_from(asked)
            .join(JobRow, or_(in_params, in_result))
            .group_by(fragment)
        )
        with session_scope() as s:
            return [
                Named(fragment=text, used_at=used_at, running=running, in_result=held)
                for text, used_at, running, held in s.execute(query)
            ]

    @cached
    def list_all(self) -> list[Job]:
        """Newest first."""
        with session_scope() as s:
            rows = (
                s.execute(select(JobRow).order_by(JobRow.created_at.desc()))
                .scalars()
                .all()
            )
            return [_job(r) for r in rows]

    @cached
    def counts(self, tasks: tuple[str, ...]) -> list[JobCount]:
        """How many done jobs each user has of each of `tasks` (sorted tuple:
        stable cache key). The admin file manager's per-user totals."""
        with session_scope() as s:
            rows = s.execute(
                select(JobRow.user_id, JobRow.task, func.count())
                .where(
                    JobRow.task.in_(tasks),
                    JobRow.user_id.is_not(None),
                    JobRow.status == "done",
                )
                .group_by(JobRow.user_id, JobRow.task)
            ).all()
        return [
            JobCount(actor=str(user_id), task=task, count=n)
            for user_id, task, n in rows
        ]

    @cached
    def list_page(
        self,
        tasks: tuple[str, ...],
        user_ids: tuple[int, ...] | None,
        offset: int,
        limit: int,
        status: str | None = None,
        hidden: bool | None = None,
    ) -> JobPage:
        """One page of the jobs whose task is in `tasks` (and that one of
        `user_ids` ran, unless None), newest first, plus how many match in
        total. Filtered and sliced in SQL: a gallery page loads its 20 rows,
        not the history.
        `hidden`: only the jobs hidden from the galleries (True), only the
        others (False), or both (None).
        `tasks` and `user_ids` are sorted tuples: a stable cache key."""
        where = _page_filter(tasks, user_ids, status, hidden)
        return _page(where, [JobRow.created_at.desc()], offset, limit)

    def page_by_use(
        self,
        tasks: tuple[str, ...],
        user_ids: tuple[int, ...] | None,
        offset: int,
        limit: int,
        hidden: bool | None = None,
    ) -> JobPage:
        """`list_page`, the job whose download was asked for or used last
        first (`downloads.last_used_at`, the newest of the job's files; a
        job with no download row: when it was started). /download-assets'
        order: a link pasted again moves its card to the first place.
        Uncached: `downloads` changes without a job write."""
        used = (
            select(func.extract("epoch", func.max(DownloadRow.last_used_at)))
            .where(DownloadRow.job_id == JobRow.id)
            .correlate(JobRow)
            .scalar_subquery()
        )
        where = _page_filter(tasks, user_ids, None, hidden)
        order = [
            func.coalesce(used, JobRow.created_at).desc(),
            JobRow.created_at.desc(),
        ]
        return _page(where, order, offset, limit)

    @cached
    def list_batch(self, task: str, user_id: int, batch: str) -> list[Job]:
        """`user_id`'s jobs of `task` queued by one submit (params `batch`),
        oldest first: the order they were pasted in."""
        with session_scope() as s:
            rows = (
                s.execute(
                    select(JobRow)
                    .where(
                        JobRow.task == task,
                        JobRow.user_id == user_id,
                        JobRow.params["batch"].astext == batch,
                    )
                    .order_by(JobRow.created_at)
                )
                .scalars()
                .all()
            )
            return [_job(r) for r in rows]

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

    @invalidates_cache
    def set_cost(
        self,
        job_id: str,
        meta: dict[str, Any],
        cost_usd: Decimal | None,
        cost_estimated: bool,
    ) -> None:
        """The job's provider requests (`meta`) and what they cost. A
        missing job: nothing happens."""
        with session_scope() as s:
            s.execute(
                update(JobRow)
                .where(JobRow.id == job_id)
                .values(meta=meta, cost_usd=cost_usd, cost_estimated=cost_estimated)
            )

    def spend(
        self, user_ids: tuple[int, ...], periods: tuple[SpendPeriod, ...]
    ) -> list[Spend]:
        """What each of `user_ids`' runs cost in each of `periods`: a run is
        a job that kept a provider request (`meta.generations`), counted in
        the period it was started in. Grouped and summed here, one row per
        person and period that has a run. Uncached: money is read fresh."""
        if not user_ids or not periods:
            return []
        period = values(
            column("name", Text),
            column("from_ts", Float),
            column("to_ts", Float),
            name="period",
        ).data([(p.name, p.from_ts, p.to_ts) for p in periods])
        query = (
            select(
                JobRow.user_id,
                period.c.name,
                func.count(),
                func.coalesce(func.sum(JobRow.cost_usd), 0),
                func.coalesce(
                    func.sum(JobRow.cost_usd).filter(JobRow.cost_estimated), 0
                ),
                func.count().filter(JobRow.cost_usd.is_(None)),
            )
            .join(
                period,
                and_(
                    JobRow.created_at >= period.c.from_ts,
                    JobRow.created_at < period.c.to_ts,
                ),
            )
            .where(
                JobRow.user_id.in_(user_ids),
                func.jsonb_array_length(JobRow.meta["generations"]) > 0,
            )
            .group_by(JobRow.user_id, period.c.name)
        )
        with session_scope() as s:
            rows = s.execute(query).all()
        return [
            Spend(
                user_id=user_id,
                period=name,
                runs=runs,
                usd=usd,
                estimated_usd=estimated,
                unpriced=unpriced,
            )
            for user_id, name, runs, usd, estimated, unpriced in rows
        ]

    def redone_links(
        self, task: str, user_id: int, links: list[str], after: float
    ) -> set[str]:
        """Which of `links` (params `link`) `user_id` queued again as `task`
        after `after` and that is still running or done."""
        if not links:
            return set()
        link = JobRow.params["link"].astext
        with session_scope() as s:
            found = s.execute(
                select(link).where(
                    JobRow.task == task,
                    JobRow.user_id == user_id,
                    JobRow.created_at > after,
                    JobRow.status.in_(("running", "done")),
                    link.in_(links),
                )
            ).scalars()
            return set(found)

    @cached
    def last_param(
        self, tasks: tuple[str, ...], user_id: int, field: str, among: tuple[str, ...]
    ) -> str | None:
        """Param `field` of `user_id`'s newest job of `tasks` whose value is
        one of `among` (sorted tuples: stable cache key), else None."""
        value = JobRow.params[field].astext
        with session_scope() as s:
            return s.execute(
                select(value)
                .where(
                    JobRow.task.in_(tasks), JobRow.user_id == user_id, value.in_(among)
                )
                .order_by(JobRow.created_at.desc())
                .limit(1)
            ).scalar()

    @cached
    def last_used_firsts(
        self, tasks: tuple[str, ...], user_id: int, field: str, limit: int
    ) -> list[str]:
        """The first file of the list param `field` (`reference_images`: the
        subject) of `user_id`'s jobs of `tasks` (sorted tuple: stable cache
        key), last used first, one path per file (`_firsts`)."""
        return self._firsts(
            tasks, user_id, field, limit, [func.max(JobRow.created_at).desc()]
        )

    @cached
    def most_used_firsts(
        self, tasks: tuple[str, ...], user_id: int, field: str, limit: int
    ) -> list[str]:
        """As `last_used_firsts`, the one in the most jobs first (a tie: the
        last used)."""
        return self._firsts(
            tasks,
            user_id,
            field,
            limit,
            [func.count().desc(), func.max(JobRow.created_at).desc()],
        )

    def _firsts(
        self,
        tasks: tuple[str, ...],
        user_id: int,
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
                        JobRow.user_id == user_id,
                        first.is_not(None),
                    )
                    .group_by(content)
                    .order_by(*order)
                    .limit(limit)
                ).scalars()
            )

    @cached
    def count_hidden(self, tasks: tuple[str, ...], user_ids: tuple[int, ...]) -> int:
        """How many of `user_ids`' jobs of `tasks` are hidden (sorted tuples:
        stable cache key) -- whether a gallery offers "Show hidden"."""
        with session_scope() as s:
            return s.execute(
                select(func.count())
                .select_from(JobRow)
                .where(
                    JobRow.task.in_(tasks),
                    JobRow.user_id.in_(user_ids),
                    JobRow.hidden_at.is_not(None),
                )
            ).scalar_one()
