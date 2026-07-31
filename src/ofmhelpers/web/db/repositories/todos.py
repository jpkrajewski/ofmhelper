"""The VA task list."""

from __future__ import annotations

import time
import uuid

from sqlalchemy import select

from ofmhelpers.web.db.models import (
    TodoRow,
)
from ofmhelpers.web.db.repositories.cached_repository import (
    CachedRepository,
    cached,
    invalidates_cache,
)
from ofmhelpers.web.db.session import session_scope


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


class TodoRepository(CachedRepository):
    cache_namespace = "todo"

    @invalidates_cache
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

    @cached
    def get(self, todo_id: str) -> dict | None:
        with session_scope() as s:
            row = s.get(TodoRow, todo_id)
            return _todo_to_dict(row) if row is not None else None

    @cached
    def list_all(self) -> list[dict]:
        """Newest first."""
        with session_scope() as s:
            rows = (
                s.execute(select(TodoRow).order_by(TodoRow.created_at.desc()))
                .scalars()
                .all()
            )
            return [_todo_to_dict(r) for r in rows]

    @invalidates_cache
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

    @invalidates_cache
    def approve(self, todo_id: str) -> bool:
        with session_scope() as s:
            row = s.get(TodoRow, todo_id)
            if row is None or not row.asset_path:
                return False
            row.approved = True
            row.rejected = False
            row.reject_comment = None
            return True

    @invalidates_cache
    def reject(self, todo_id: str, comment: str) -> bool:
        with session_scope() as s:
            row = s.get(TodoRow, todo_id)
            if row is None or not row.asset_path:
                return False
            row.approved = False
            row.rejected = True
            row.reject_comment = comment
            return True

    @invalidates_cache
    def set_drive_upload_job(self, todo_id: str, job_id: str) -> bool:
        with session_scope() as s:
            row = s.get(TodoRow, todo_id)
            if row is None:
                return False
            row.drive_upload_job_id = job_id
            return True

    @invalidates_cache
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

    @invalidates_cache
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

    @invalidates_cache
    def toggle(self, todo_id: str) -> bool:
        with session_scope() as s:
            row = s.get(TodoRow, todo_id)
            if row is None:
                return False
            row.checked = not row.checked
            return True

    @invalidates_cache
    def delete(self, todo_id: str) -> bool:
        with session_scope() as s:
            row = s.get(TodoRow, todo_id)
            if row is None:
                return False
            s.delete(row)
            return True


# The optional per-account details the edit form writes as one block.
_ACCOUNT_DETAIL_FIELDS = ("owner", "phone", "sim_number", "password", "email")
