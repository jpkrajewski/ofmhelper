"""
The ONLY module allowed to touch the database. web/jobs.py, web/todos.py and
web/approval_tokens.py keep their existing public function signatures but
delegate their bodies to the repositories here, so the ~13 routers that call
those functions never change.

Each method runs in its own `session_scope` unit of work. Status/field
updates are single UPDATE statements (atomic) -- the read-modify-write race
the old JSON files documented (two jobs finishing at once losing an update)
is gone.

Return values are plain dicts in the exact shape the old in-memory dicts had,
so callers (templates, task_helpers, routers) are unaffected.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from sqlalchemy import delete, select, update

from ofmhelpers.config import settings
from ofmhelpers.web.db.models import ApprovalTokenRow, JobRow, TodoRow
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


class JobRepository:
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

    def get(self, job_id: str) -> dict | None:
        with session_scope() as s:
            row = s.get(JobRow, job_id)
            return _job_to_dict(row) if row is not None else None

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

    def set_preview(self, job_id: str, preview: dict) -> None:
        # UPDATE on a missing id touches 0 rows -- same no-op the old
        # set_job_preview did when the job wasn't found.
        with session_scope() as s:
            s.execute(update(JobRow).where(JobRow.id == job_id).values(preview=preview))

    def update_result(self, job_id: str, result: Any) -> None:
        with session_scope() as s:
            s.execute(update(JobRow).where(JobRow.id == job_id).values(result=result))

    def delete(self, job_id: str) -> None:
        with session_scope() as s:
            s.execute(delete(JobRow).where(JobRow.id == job_id))

    def list_all(self) -> list[dict]:
        """Newest first."""
        with session_scope() as s:
            rows = (
                s.execute(select(JobRow).order_by(JobRow.created_at.desc()))
                .scalars()
                .all()
            )
            return [_job_to_dict(r) for r in rows]


def _todo_to_dict(row: TodoRow) -> dict:
    return {
        "id": row.id,
        "model_name": row.model_name,
        "url": row.url,
        "comments": row.comments,
        "checked": row.checked,
        "created_at": row.created_at,
        "created_by": row.created_by,
        "asset_path": row.asset_path,
        "asset_name": row.asset_name,
        "approved": row.approved,
        "rejected": row.rejected,
        "reject_comment": row.reject_comment,
        "drive_file_id": row.drive_file_id,
        "drive_uploaded_at": row.drive_uploaded_at,
        "drive_upload_job_id": row.drive_upload_job_id,
    }


class TodoRepository:
    def add(
        self, model_name: str, url: str, comments: str, created_by: str | None
    ) -> dict:
        row = TodoRow(
            id=uuid.uuid4().hex[:8],
            model_name=model_name,
            url=url,
            comments=comments,
            checked=False,
            created_at=time.time(),
            created_by=created_by,
        )
        with session_scope() as s:
            s.add(row)
            s.flush()
            return _todo_to_dict(row)

    def get(self, todo_id: str) -> dict | None:
        with session_scope() as s:
            row = s.get(TodoRow, todo_id)
            return _todo_to_dict(row) if row is not None else None

    def list_all(self) -> list[dict]:
        """Newest first."""
        with session_scope() as s:
            rows = (
                s.execute(select(TodoRow).order_by(TodoRow.created_at.desc()))
                .scalars()
                .all()
            )
            return [_todo_to_dict(r) for r in rows]

    def attach_asset(self, todo_id: str, asset_path: str, asset_name: str) -> bool:
        with session_scope() as s:
            row = s.get(TodoRow, todo_id)
            if row is None:
                return False
            # A new asset resets any prior approval/rejection/upload -- those
            # applied to the old file, not this one.
            row.asset_path = asset_path
            row.asset_name = asset_name
            row.approved = False
            row.rejected = False
            row.reject_comment = None
            row.drive_file_id = None
            row.drive_uploaded_at = None
            row.drive_upload_job_id = None
            return True

    def approve(self, todo_id: str) -> bool:
        with session_scope() as s:
            row = s.get(TodoRow, todo_id)
            if row is None or not row.asset_path:
                return False
            row.approved = True
            row.rejected = False
            row.reject_comment = None
            return True

    def reject(self, todo_id: str, comment: str) -> bool:
        with session_scope() as s:
            row = s.get(TodoRow, todo_id)
            if row is None or not row.asset_path:
                return False
            row.approved = False
            row.rejected = True
            row.reject_comment = comment
            return True

    def set_drive_upload_job(self, todo_id: str, job_id: str) -> bool:
        with session_scope() as s:
            row = s.get(TodoRow, todo_id)
            if row is None:
                return False
            row.drive_upload_job_id = job_id
            return True

    def mark_uploaded(self, todo_id: str, asset_path: str, drive_file_id: str) -> bool:
        with session_scope() as s:
            row = s.get(TodoRow, todo_id)
            if row is None:
                return False
            # asset_path must match the todo's *current* asset -- a slow upload
            # of a since-replaced file must not mark the new asset as done.
            if row.asset_path != asset_path:
                return False
            row.drive_file_id = drive_file_id
            row.drive_uploaded_at = time.time()
            return True

    def import_many(self, rows: list[dict], created_by: str | None) -> int:
        """Bulk insert already-validated fresh todos (see todos.import_todos)."""
        with session_scope() as s:
            for r in rows:
                s.add(
                    TodoRow(
                        id=uuid.uuid4().hex[:8],
                        model_name=r["model_name"],
                        url=r["url"],
                        comments=r["comments"],
                        checked=False,
                        created_at=time.time(),
                        created_by=created_by,
                    )
                )
            return len(rows)

    def toggle(self, todo_id: str) -> bool:
        with session_scope() as s:
            row = s.get(TodoRow, todo_id)
            if row is None:
                return False
            row.checked = not row.checked
            return True

    def delete(self, todo_id: str) -> bool:
        with session_scope() as s:
            row = s.get(TodoRow, todo_id)
            if row is None:
                return False
            s.delete(row)
            return True


def _token_to_dict(row: ApprovalTokenRow) -> dict:
    return {
        "token": row.token,
        "todo_id": row.todo_id,
        "asset_path": row.asset_path,
        "created_at": row.created_at,
        "expires_at": row.expires_at,
        "used_at": row.used_at,
    }


class ApprovalTokenRepository:
    def _purge_expired(self, session, now: float) -> None:
        session.execute(
            delete(ApprovalTokenRow).where(ApprovalTokenRow.expires_at < now)
        )

    def create(self, todo_id: str, asset_path: str, ttl_seconds: int) -> str:
        import secrets

        token = secrets.token_urlsafe(32)
        now = time.time()
        with session_scope() as s:
            self._purge_expired(s, now)
            s.add(
                ApprovalTokenRow(
                    token=token,
                    todo_id=todo_id,
                    asset_path=asset_path,
                    created_at=now,
                    expires_at=now + ttl_seconds,
                    used_at=None,
                )
            )
        return token

    def get(self, token: str) -> dict | None:
        with session_scope() as s:
            row = s.get(ApprovalTokenRow, token)
            return _token_to_dict(row) if row is not None else None

    def consume(self, token: str, current_asset_path: str) -> str:
        """Validate and, only on success, mark used in the same transaction.
        Returns "ok" / "not_found" / "expired" / "used" / "stale"."""
        now = time.time()
        with session_scope() as s:
            row = s.get(ApprovalTokenRow, token)
            if row is None:
                return "not_found"
            if row.used_at is not None:
                return "used"
            if row.expires_at < now:
                return "expired"
            if row.asset_path != current_asset_path:
                return "stale"
            row.used_at = now
            return "ok"
