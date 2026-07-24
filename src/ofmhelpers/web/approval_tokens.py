"""
ofmhelpers/web/approval_tokens.py

Single-use "magic link" tokens for approving a todo's uploaded asset without
logging in (see routers/approve.py). JSON-persisted, same tradeoff as
web/todos.py and web/jobs.py: no locking, read-modify-write on every change,
fine for a handful of VAs on one machine.

A token snapshots the asset_path it was issued for. If the VA replaces the
asset after the Discord notification went out, the snapshot no longer matches
the todo's current asset_path -- consume() reports that as "stale" rather than
approving the wrong file. This is the same race todos.mark_uploaded() already
guards against for the Drive-upload job.
"""

from __future__ import annotations

import json
import os
import secrets
import time
from pathlib import Path

STORE_FILE = Path(os.getenv("OFM_APPROVAL_TOKENS_FILE", "uploads/approval_tokens.json"))

TOKEN_TTL_SECONDS = 3 * 24 * 3600  # 3 days


def _load() -> list[dict]:
    if not STORE_FILE.exists():
        return []
    try:
        return json.loads(STORE_FILE.read_text())
    except json.JSONDecodeError:
        return []


def _save(items: list[dict]) -> None:
    now = time.time()
    items = [t for t in items if t["expires_at"] >= now]
    STORE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STORE_FILE.write_text(json.dumps(items, indent=2))


def create_token(todo_id: str, asset_path: str) -> str:
    items = _load()
    token = secrets.token_urlsafe(32)
    now = time.time()
    items.append(
        {
            "token": token,
            "todo_id": todo_id,
            "asset_path": asset_path,
            "created_at": now,
            "expires_at": now + TOKEN_TTL_SECONDS,
            "used_at": None,
        }
    )
    _save(items)
    return token


def get_token(token: str) -> dict | None:
    for t in _load():
        if t["token"] == token:
            return t
    return None


def consume(token: str, current_asset_path: str) -> str:
    """Validates and, only on success, marks the token used in the same
    read-modify-write pass. Returns "ok" / "not_found" / "expired" / "used" /
    "stale"."""
    items = _load()
    now = time.time()
    for t in items:
        if t["token"] != token:
            continue
        if t["used_at"] is not None:
            return "used"
        if t["expires_at"] < now:
            return "expired"
        if t["asset_path"] != current_asset_path:
            return "stale"
        t["used_at"] = now
        _save(items)
        return "ok"
    return "not_found"
