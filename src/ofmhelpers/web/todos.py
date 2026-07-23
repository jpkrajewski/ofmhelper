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
    }
    items.append(todo)
    _save(items)
    return todo


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
