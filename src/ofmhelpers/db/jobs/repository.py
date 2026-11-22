import time
import uuid
from typing import Any
lazy from collections.abc import Collection, Sequence
lazy from decimal import Decimal

from sqlalchemy import (
    ColumnElement,
    Date,
    Float,
    Text,
    and_,
    case,
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
from sqlalchemy.dialects.postgresql import JSONB, aggregate_order_by, array, array_agg

from ofmhelpers.config import settings
from ofmhelpers.db.cached_repository import CachedRepository, cached, invalidates_cache
from ofmhelpers.db.jobs.schema import (
    Job,
    JobCount,
    JobPage,
    JobParams,
    Named,
    RecordedJob,
    SessionSpan,
    Spend,
    SpendPeriod,
    UserDay,
)
from ofmhelpers.db.models import DownloadRow, DriveUploadRow, JobRow
from ofmhelpers.db.session import session_scope

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


_MIN_DELETE_PREFIX = 4
_SESSION_REACH_S = 86400


def _page_filter(
    tasks: Sequence[str],
    user_ids: Sequence[int] | None,
    status: str | None,
    hidden: bool | None,
    since: float | None = None,
    until: float | None = None,
) -> list[ColumnElement[bool]]:
    where: list[ColumnElement[bool]] = [JobRow.task.in_(tasks)]
    if since is not None:
        where.append(JobRow.created_at >= since)
    if until is not None:
        where.append(JobRow.created_at < until)
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
        created_at: float | None = None,
    ) -> str:
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
                    created_at=created_at if created_at is not None else time.time(),
                    preview=None,
                )
            )
            s.flush()
            self._enforce_cap(s, task_name, user_id)
        return job_id

    @invalidates_cache
    def add_recorded(self, rows: list[RecordedJob]) -> None:
        with session_scope() as s:
            s.add_all(
                JobRow(
                    id=row.id,
                    task=row.task,
                    user_id=row.user_id,
                    status=row.status,
                    created_at=row.created_at,
                    finished_at=row.finished_at,
                    params=row.params,
                    result=row.result,
                    error=row.error,
                    preview=None,
                    meta=row.meta.model_dump(mode="json"),
                    cost_usd=row.cost_usd,
                    cost_estimated=row.cost_estimated,
                )
                for row in rows
            )

    @invalidates_cache
    def delete_like(self, prefix: str) -> int:
        if len(prefix) < _MIN_DELETE_PREFIX:
            msg = f"refusing to delete jobs by the prefix {prefix!r}"
            raise ValueError(msg)
        with session_scope() as s:
            gone = s.execute(
                delete(JobRow)
                .where(JobRow.id.startswith(prefix, autoescape=True))
                .returning(JobRow.id)
            )
            return len(gone.all())

    def _enforce_cap(self, session, task: str, user_id: int | None) -> None:
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
            values["preview"] = None
        if result is not _UNSET:
            values["result"] = result
        if error is not _UNSET:
            values["error"] = error
        with session_scope() as s:
            s.execute(update(JobRow).where(JobRow.id == job_id).values(**values))

    @invalidates_cache
    def reopen_settled(self, job_id: str) -> bool:
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
        with session_scope() as s:
            s.execute(update(JobRow).where(JobRow.id == job_id).values(preview=preview))

    @invalidates_cache
    def update_result(self, job_id: str, result: Any) -> None:
        with session_scope() as s:
            s.execute(update(JobRow).where(JobRow.id == job_id).values(result=result))

    @invalidates_cache
    def set_hidden(self, job_id: str, hidden_at: float | None) -> None:
        with session_scope() as s:
            s.execute(
                update(JobRow).where(JobRow.id == job_id).values(hidden_at=hidden_at)
            )

    @invalidates_cache
    def delete(self, job_id: str) -> None:
        with session_scope() as s:
            s.execute(delete(JobRow).where(JobRow.id == job_id))

    def list_running(self) -> list[Job]:
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
        if not fragments:
            return []
        asked = values(column("fragment", Text), name="asked").data(
            [(fragment,) for fragment in fragments]
        )
        as_text = (
            select(
                JobRow.created_at,
                JobRow.status,
                cast(JobRow.params, Text).label("params"),
                func.coalesce(cast(JobRow.result, Text), "").label("result"),
            )
            .cte("as_text")
            .prefix_with("MATERIALIZED")
        )
        fragment = asked.c.fragment
        in_params = func.strpos(as_text.c.params, fragment) > 0
        in_result = func.strpos(as_text.c.result, fragment) > 0
        query = (
            select(
                fragment,
                func.max(as_text.c.created_at).filter(in_params),
                func.coalesce(
                    func.bool_or(as_text.c.status == "running").filter(in_params),
                    False,
                ),
                func.bool_or(in_result),
            )
            .select_from(asked)
            .join(as_text, or_(in_params, in_result))
            .group_by(fragment)
        )
        with session_scope() as s:
            return [
                Named(fragment=text, used_at=used_at, running=running, in_result=held)
                for text, used_at, running, held in s.execute(query)
            ]

    @cached
    def list_all(self) -> list[Job]:
        with session_scope() as s:
            rows = (
                s.execute(select(JobRow).order_by(JobRow.created_at.desc()))
                .scalars()
                .all()
            )
            return [_job(r) for r in rows]

    @cached
    def counts(self, tasks: Sequence[str]) -> list[JobCount]:
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
        tasks: Sequence[str],
        user_ids: Sequence[int] | None,
        offset: int,
        limit: int,
        status: str | None = None,
        hidden: bool | None = None,
        since: float | None = None,
        until: float | None = None,
    ) -> JobPage:
        where = _page_filter(tasks, user_ids, status, hidden, since, until)
        return _page(where, [JobRow.created_at.desc()], offset, limit)

    def page_by_use(
        self,
        tasks: Sequence[str],
        user_ids: Sequence[int] | None,
        offset: int,
        limit: int,
        hidden: bool | None = None,
        since: float | None = None,
        until: float | None = None,
    ) -> JobPage:
        used = (
            select(func.extract("epoch", func.max(DownloadRow.last_used_at)))
            .where(DownloadRow.job_id == JobRow.id)
            .correlate(JobRow)
            .scalar_subquery()
        )
        where = _page_filter(tasks, user_ids, None, hidden, since, until)
        order = [
            func.coalesce(used, JobRow.created_at).desc(),
            JobRow.created_at.desc(),
        ]
        return _page(where, order, offset, limit)

    def page_not_uploaded(
        self,
        tasks: Sequence[str],
        user_ids: Sequence[int] | None,
        offset: int,
        limit: int,
        hidden: bool | None = None,
        since: float | None = None,
        until: float | None = None,
    ) -> JobPage:
        uploaded = (
            select(DriveUploadRow.id)
            .where(
                DriveUploadRow.job_id == JobRow.id,
                DriveUploadRow.status == "done",
            )
            .correlate(JobRow)
            .exists()
        )
        where = _page_filter(tasks, user_ids, "done", hidden, since, until)
        where.append(~uploaded)
        return _page(where, [JobRow.created_at.desc()], offset, limit)

    @cached
    def list_batch(self, task: str, user_id: int, batch: str) -> list[Job]:
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

    def running_with_links(
        self, tasks: Collection[str], user_id: int, links: list[str]
    ) -> list[Job]:
        if not links:
            return []
        with session_scope() as s:
            rows = s.execute(
                select(JobRow)
                .where(
                    JobRow.task.in_(tasks),
                    JobRow.user_id == user_id,
                    JobRow.status == "running",
                    JobRow.params["urls"].has_any(array(links)),
                )
                .order_by(JobRow.created_at)
            ).scalars()
            return [_job(r) for r in rows]

    @invalidates_cache
    def merge_params(self, job_id: str, values: dict[str, Any]) -> None:
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
        with session_scope() as s:
            s.execute(
                update(JobRow)
                .where(JobRow.id == job_id)
                .values(meta=meta, cost_usd=cost_usd, cost_estimated=cost_estimated)
            )

    def days(
        self,
        user_ids: Sequence[int],
        from_ts: float,
        to_ts: float,
        zone: str,
        reel_tasks: Sequence[str],
    ) -> list[UserDay]:
        if not user_ids:
            return []
        day = cast(
            func.timezone(zone, func.to_timestamp(JobRow.created_at)), Date
        ).label("day")
        at = JobRow.created_at

        query = (
            select(
                JobRow.user_id,
                day,
                func.min(at).filter(JobRow.task == "login"),
                func.max(at).filter(JobRow.task == "login"),
                func.max(at).filter(JobRow.task == "logout"),
                func.max(at).filter(JobRow.task == "idle_logout"),
                func.count().filter(
                    JobRow.task.in_(reel_tasks), JobRow.status == "done"
                ),
                func.sum(JobRow.cost_usd),
            )
            .where(JobRow.user_id.in_(user_ids), at >= from_ts, at < to_ts)
            .group_by(JobRow.user_id, day)
            .order_by(day.desc(), JobRow.user_id)
        )
        with session_scope() as s:
            return [
                UserDay(
                    user_id=user_id,
                    day=on,
                    first_login_at=first_login,
                    last_login_at=last_login,
                    logout_at=logout,
                    idle_logout_at=idle,
                    reels=reels,
                    usd=usd,
                )
                for (
                    user_id,
                    on,
                    first_login,
                    last_login,
                    logout,
                    idle,
                    reels,
                    usd,
                ) in s.execute(query)
            ]

    def sessions(
        self, user_ids: Sequence[int], from_ts: float, to_ts: float
    ) -> list[SessionSpan]:
        if not user_ids:
            return []
        events = ("login", "logout", "idle_logout")
        by, at = JobRow.user_id, JobRow.created_at
        ev = (
            select(
                JobRow.user_id,
                JobRow.task,
                JobRow.created_at,
                func.lead(at).over(partition_by=by, order_by=at).label("next_at"),
                func.lead(JobRow.task)
                .over(partition_by=by, order_by=at)
                .label("next_task"),
            )
            .where(
                JobRow.user_id.in_(user_ids),
                JobRow.task.in_(events),
                JobRow.created_at >= from_ts - _SESSION_REACH_S,
                JobRow.created_at < to_ts + _SESSION_REACH_S,
            )
            .subquery()
        )
        work = JobRow.__table__.alias("work")
        last_job = (
            select(func.max(work.c.created_at))
            .where(
                work.c.user_id == ev.c.user_id,
                work.c.created_at > ev.c.created_at,
                or_(ev.c.next_at.is_(None), work.c.created_at < ev.c.next_at),
                work.c.task.not_in(events),
            )
            .scalar_subquery()
        )
        ended = case(
            (ev.c.next_task.in_(("logout", "idle_logout")), ev.c.next_at), else_=None
        )
        query = (
            select(ev.c.user_id, ev.c.created_at, ended, ev.c.next_at, last_job)
            .where(
                ev.c.task == "login",
                ev.c.created_at < to_ts,
                or_(ev.c.next_at.is_(None), ev.c.next_at > from_ts),
            )
            .order_by(ev.c.user_id, ev.c.created_at)
        )
        with session_scope() as s:
            return [
                SessionSpan(
                    user_id=user_id,
                    start_at=start,
                    end_at=end,
                    next_at=after,
                    last_job_at=last,
                )
                for user_id, start, end, after, last in s.execute(query)
            ]

    def spend(
        self, user_ids: Sequence[int], periods: tuple[SpendPeriod, ...]
    ) -> list[Spend]:
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
        self,
        tasks: Sequence[str],
        user_ids: Sequence[int],
        field: str,
        among: tuple[str, ...],
    ) -> str | None:
        value = JobRow.params[field].astext
        with session_scope() as s:
            return s.execute(
                select(value)
                .where(
                    JobRow.task.in_(tasks),
                    JobRow.user_id.in_(user_ids),
                    value.in_(among),
                )
                .order_by(JobRow.created_at.desc())
                .limit(1)
            ).scalar()

    @cached
    def last_used_firsts(
        self, tasks: Sequence[str], user_ids: Sequence[int], field: str, limit: int
    ) -> list[str]:
        return self._firsts(
            tasks, user_ids, field, limit, [func.max(JobRow.created_at).desc()]
        )

    @cached
    def most_used_firsts(
        self, tasks: Sequence[str], user_ids: Sequence[int], field: str, limit: int
    ) -> list[str]:
        return self._firsts(
            tasks,
            user_ids,
            field,
            limit,
            [func.count().desc(), func.max(JobRow.created_at).desc()],
        )

    def _firsts(
        self,
        tasks: Sequence[str],
        user_ids: Sequence[int],
        field: str,
        limit: int,
        order: list[Any],
    ) -> list[str]:
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
                        JobRow.user_id.in_(user_ids),
                        first.is_not(None),
                    )
                    .group_by(content)
                    .order_by(*order)
                    .limit(limit)
                ).scalars()
            )

    @cached
    def count_hidden(self, tasks: Sequence[str], user_ids: Sequence[int]) -> int:
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
