"""
ofmhelpers/web/stores/todos.py

Persisted todo list: admins add "go do this" tasks (a model name, a link to
replicate, and comments) for VAs to see. Now backed by Postgres (see web/db/)
instead of a single JSON file; the public API is unchanged so routers/workflow/todo.py
and routers/workflow/approve.py are untouched.

Persistence matters here (unlike job *history*, losing a VA's outstanding task
list on restart would be a real problem) -- Postgres gives that durably, plus
atomic per-row updates instead of the old lock-free rewrite of the whole file.
"""

from __future__ import annotations

from functools import lru_cache

from ofmhelpers.web.db.repositories import TodoRepository


@lru_cache(maxsize=1)
def _repository() -> TodoRepository:
    """The process-wide todo repository, built on first use rather than at
    import. Lazy because constructing it binds a Redis connection for its
    cache, and at import time OFM_REDIS_URL may not be its final value yet."""
    return TodoRepository()


def list_todos() -> list[dict]:
    """Newest first."""
    return _repository().list_all()


def add_todo(model_name: str, url: str, comments: str, created_by: str | None) -> dict:
    return _repository().add(model_name, url, comments, created_by)


def get_todo(todo_id: str) -> dict | None:
    return _repository().get(todo_id)


def attach_asset(todo_id: str, asset_path: str, asset_name: str) -> bool:
    """VA uploads a ready asset for a task. A new asset resets any prior
    approval/rejection/upload -- those applied to the old file, not this
    one."""
    return _repository().attach_asset(todo_id, asset_path, asset_name)


def approve_todo(todo_id: str) -> bool:
    """Admin approves the attached asset. Returns False if the todo doesn't
    exist or has no asset attached yet."""
    return _repository().approve(todo_id)


def reject_todo(todo_id: str, comment: str) -> bool:
    """Admin rejects the attached asset with a comment telling the VA what
    to fix. Returns False if the todo doesn't exist or has no asset attached
    yet."""
    return _repository().reject(todo_id, comment)


def set_drive_upload_job(todo_id: str, job_id: str) -> bool:
    """Records which background job (see web/stores/jobs.py) is currently uploading
    this todo's asset to Drive, so the list page can show its live status."""
    return _repository().set_drive_upload_job(todo_id, job_id)


def mark_uploaded(todo_id: str, asset_path: str, drive_file_id: str) -> bool:
    """asset_path must match the todo's *current* asset -- the upload runs in
    a background job, so by the time it finishes a VA may have replaced the
    asset (attach_asset resets asset_path). Without this check, a slow
    upload of the old file could land after a replacement and incorrectly
    mark the new, never-uploaded asset as done."""
    return _repository().mark_uploaded(todo_id, asset_path, drive_file_id)


def import_todos(entries: list[dict], created_by: str | None) -> int:
    """Bulk-adds todos parsed from an uploaded JSON file (e.g. a previous
    /todo/export). Each entry needs at least model_name + url; anything else
    in it (id/checked/created_at/created_by) is ignored -- imported rows
    always become fresh tasks, same as the manual add form, so a stale or
    edited-by-hand upload can never overwrite or resurrect existing state.
    All-or-nothing: raises ValueError (naming the offending item) before
    writing anything if any entry is invalid.
    """
    validated = []
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            # ValueError, not TypeError: the /todo/import route maps this
            # whole validation pass onto a single HTTP 400.
            msg = f"item {i} is not a JSON object"
            raise ValueError(msg)  # noqa: TRY004
        model_name = str(entry.get("model_name") or "").strip()
        url = str(entry.get("url") or "").strip()
        if not model_name or not url:
            msg = f"item {i} is missing model_name or url"
            raise ValueError(msg)
        comments = str(entry.get("comments") or "").strip()
        validated.append({"model_name": model_name, "url": url, "comments": comments})

    return _repository().import_many(validated, created_by)


def toggle_todo(todo_id: str) -> bool:
    """Flips checked/unchecked. Returns False if no such todo exists."""
    return _repository().toggle(todo_id)


def delete_todo(todo_id: str) -> bool:
    """Returns False if no such todo exists."""
    return _repository().delete(todo_id)
