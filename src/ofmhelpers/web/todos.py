"""
ofmhelpers/web/todos.py

Simple persisted todo list: admins add "go do this" tasks (a model name, a
link to replicate, and comments) for VAs to see. Persisted as a single JSON
file -- unlike jobs.py's in-memory JOBS (fine to lose on restart, they're
just a run history), a VA's outstanding task list disappearing on every
redeploy would actually be a problem, so this is written to disk on every
change.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

STORE_FILE = Path(os.getenv("OFM_TODO_FILE", "uploads/todos.json"))


def _load() -> list[dict]:
    if not STORE_FILE.exists():
        return []
    try:
        return json.loads(STORE_FILE.read_text())
    except json.JSONDecodeError:
        return []


def _save(items: list[dict]) -> None:
    STORE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STORE_FILE.write_text(json.dumps(items, indent=2))


def list_todos() -> list[dict]:
    """Newest first."""
    return sorted(_load(), key=lambda t: t["created_at"], reverse=True)


def add_todo(model_name: str, url: str, comments: str, created_by: str | None) -> dict:
    items = _load()
    todo = {
        "id": uuid.uuid4().hex[:8],
        "model_name": model_name,
        "url": url,
        "comments": comments,
        "checked": False,
        "created_at": time.time(),
        "created_by": created_by,
        "asset_path": None,
        "asset_name": None,
        "approved": False,
        "drive_file_id": None,
        "drive_uploaded_at": None,
        "drive_upload_job_id": None,
    }
    items.append(todo)
    _save(items)
    return todo


def get_todo(todo_id: str) -> dict | None:
    for t in _load():
        if t["id"] == todo_id:
            return t
    return None


def attach_asset(todo_id: str, asset_path: str, asset_name: str) -> bool:
    """VA uploads a ready asset for a task. A new asset resets any prior
    approval/upload -- those applied to the old file, not this one."""
    items = _load()
    for t in items:
        if t["id"] == todo_id:
            t["asset_path"] = asset_path
            t["asset_name"] = asset_name
            t["approved"] = False
            t["drive_file_id"] = None
            t["drive_uploaded_at"] = None
            t["drive_upload_job_id"] = None
            _save(items)
            return True
    return False


def approve_todo(todo_id: str) -> bool:
    """Admin approves the attached asset. Returns False if the todo doesn't
    exist or has no asset attached yet."""
    items = _load()
    for t in items:
        if t["id"] == todo_id:
            if not t.get("asset_path"):
                return False
            t["approved"] = True
            _save(items)
            return True
    return False


def set_drive_upload_job(todo_id: str, job_id: str) -> bool:
    """Records which background job (see web/jobs.py) is currently uploading
    this todo's asset to Drive, so the list page can show its live status."""
    items = _load()
    for t in items:
        if t["id"] == todo_id:
            t["drive_upload_job_id"] = job_id
            _save(items)
            return True
    return False


def mark_uploaded(todo_id: str, asset_path: str, drive_file_id: str) -> bool:
    """asset_path must match the todo's *current* asset -- the upload runs in
    a background job, so by the time it finishes a VA may have replaced the
    asset (attach_asset resets asset_path). Without this check, a slow
    upload of the old file could land after a replacement and incorrectly
    mark the new, never-uploaded asset as done."""
    items = _load()
    for t in items:
        if t["id"] == todo_id:
            if t.get("asset_path") != asset_path:
                return False
            t["drive_file_id"] = drive_file_id
            t["drive_uploaded_at"] = time.time()
            _save(items)
            return True
    return False


def import_todos(entries: list[dict], created_by: str | None) -> int:
    """Bulk-adds todos parsed from an uploaded JSON file (e.g. a previous
    /todo/export). Each entry needs at least model_name + url; anything else
    in it (id/checked/created_at/created_by) is ignored -- imported rows
    always become fresh tasks, same as the manual add form, so a stale or
    edited-by-hand upload can never overwrite or resurrect existing state.
    All-or-nothing: raises ValueError (naming the offending item) before
    writing anything if any entry is invalid.
    """
    new_items = []
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(f"item {i} is not a JSON object")
        model_name = str(entry.get("model_name") or "").strip()
        url = str(entry.get("url") or "").strip()
        if not model_name or not url:
            raise ValueError(f"item {i} is missing model_name or url")
        comments = str(entry.get("comments") or "").strip()
        new_items.append(
            {
                "id": uuid.uuid4().hex[:8],
                "model_name": model_name,
                "url": url,
                "comments": comments,
                "checked": False,
                "created_at": time.time(),
                "created_by": created_by,
            }
        )

    items = _load()
    items.extend(new_items)
    _save(items)
    return len(new_items)


def toggle_todo(todo_id: str) -> bool:
    """Flips checked/unchecked. Returns False if no such todo exists."""
    items = _load()
    for t in items:
        if t["id"] == todo_id:
            t["checked"] = not t["checked"]
            _save(items)
            return True
    return False


def delete_todo(todo_id: str) -> bool:
    """Returns False if no such todo exists."""
    items = _load()
    remaining = [t for t in items if t["id"] != todo_id]
    if len(remaining) == len(items):
        return False
    _save(remaining)
    return True
