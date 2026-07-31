"""Background-job history: what every generation/download tool writes."""

from __future__ import annotations

import time
import uuid
from typing import Any

from sqlalchemy import delete, select, update

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
        values: dict[str, Any] = {"status": status}
        if result is not _UNSET:
            values["result"] = result
        if error is not _UNSET:
            values["error"] = error
        with session_scope() as s:
            s.execute(update(JobRow).where(JobRow.id == job_id).values(**values))

    @invalidates_cache
    def set_preview(self, job_id: str, preview: dict) -> None:
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
