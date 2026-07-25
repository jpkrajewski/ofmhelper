"""
ofmhelpers/web/todos.py

Persisted todo list: admins add "go do this" tasks (a model name, a link to
replicate, and comments) for VAs to see. Now backed by Postgres (see web/db/)
instead of a single JSON file; the public API is unchanged so routers/todo.py
and routers/approve.py are untouched.

Persistence matters here (unlike job *history*, losing a VA's outstanding task
list on restart would be a real problem) -- Postgres gives that durably, plus
atomic per-row updates instead of the old lock-free rewrite of the whole file.
"""

from __future__ import annotations

from ofmhelpers.web.db.repository import TodoRepository

_repo = TodoRepository()


def list_todos() -> list[dict]:
    """Newest first."""
    return _repo.list_all()


def add_todo(model_name: str, url: str, comments: str, created_by: str | None) -> dict:
    return _repo.add(model_name, url, comments, created_by)


def get_todo(todo_id: str) -> dict | None:
    return _repo.get(todo_id)


def attach_asset(todo_id: str, asset_path: str, asset_name: str) -> bool:
    """VA uploads a ready asset for a task. A new asset resets any prior
    approval/rejection/upload -- those applied to the old file, not this
    one."""
    return _repo.attach_asset(todo_id, asset_path, asset_name)


def approve_todo(todo_id: str) -> bool:
    """Admin approves the attached asset. Returns False if the todo doesn't
    exist or has no asset attached yet."""
    return _repo.approve(todo_id)


def reject_todo(todo_id: str, comment: str) -> bool:
    """Admin rejects the attached asset with a comment telling the VA what
    to fix. Returns False if the todo doesn't exist or has no asset attached
    yet."""
    return _repo.reject(todo_id, comment)


def set_drive_upload_job(todo_id: str, job_id: str) -> bool:
    """Records which background job (see web/jobs.py) is currently uploading
    this todo's asset to Drive, so the list page can show its live status."""
    return _repo.set_drive_upload_job(todo_id, job_id)


def mark_uploaded(todo_id: str, asset_path: str, drive_file_id: str) -> bool:
    """asset_path must match the todo's *current* asset -- the upload runs in
    a background job, so by the time it finishes a VA may have replaced the
    asset (attach_asset resets asset_path). Without this check, a slow
    upload of the old file could land after a replacement and incorrectly
    mark the new, never-uploaded asset as done."""
    return _repo.mark_uploaded(todo_id, asset_path, drive_file_id)


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
            raise ValueError(f"item {i} is not a JSON object")
        model_name = str(entry.get("model_name") or "").strip()
        url = str(entry.get("url") or "").strip()
        if not model_name or not url:
            raise ValueError(f"item {i} is missing model_name or url")
        comments = str(entry.get("comments") or "").strip()
        validated.append({"model_name": model_name, "url": url, "comments": comments})

    return _repo.import_many(validated, created_by)


def toggle_todo(todo_id: str) -> bool:
    """Flips checked/unchecked. Returns False if no such todo exists."""
    return _repo.toggle(todo_id)


def delete_todo(todo_id: str) -> bool:
    """Returns False if no such todo exists."""
    return _repo.delete(todo_id)
