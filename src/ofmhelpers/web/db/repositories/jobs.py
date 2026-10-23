"""Background-job history: what every generation/download tool writes."""

from __future__ import annotations

import time
import uuid
from typing import Any

from sqlalchemy import ColumnElement, delete, func, select, update

from ofmhelpers.config import settings
from ofmhelpers.web.db.models import (
    JobRow,
)
from ofmhelpers.web.db.repositories.cached_repository import (
    CachedRepository,
    cached,
    invalidates_cache,
)
from ofmhelpers.web.db.session import session_scope

# Sentinel so update_status can tell "leave this column alone" apart from
# "set it to None" (a failed job explicitly clears result, etc.).
_UNSET: Any = object()


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
            self._enforce_cap(s)
        return job_id

    def _enforce_cap(self, session) -> None:
        """Keep the history from growing forever -- drop the oldest rows beyond
        settings.web.max_jobs, same cap the JSON store enforced on every save."""
        cap = settings.web.max_jobs
        stale_ids = (
            session.execute(
                select(JobRow.id).order_by(JobRow.created_at.desc()).offset(cap)
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
        error: Any = _UNSET,
    ) -> None:
        finished = status in ("done", "failed")
        values: dict[str, Any] = {
            "status": status,
            "finished_at": time.time() if finished else None,
        }
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
    ) -> dict:
        """One page of the jobs whose task is in `tasks` (and that `actor` ran,
        unless None), newest first, plus how many match in total. Filtered and
        sliced in SQL: a gallery page loads its 20 rows, not the history.
        `tasks` is a tuple so the cache key is stable -- callers sort it."""
        where: list[ColumnElement[bool]] = [JobRow.task.in_(tasks)]
        if actor is not None:
            where.append(JobRow.actor == actor)
        if status is not None:
            where.append(JobRow.status == status)
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
