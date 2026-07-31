"""
ofmhelpers/web/stores/approval_tokens.py

Single-use "magic link" tokens for approving a todo's uploaded asset without
logging in (see routers/workflow/approve.py). Now backed by Postgres (see web/db/)
instead of a JSON file; the public API is unchanged.

A token snapshots the asset_path it was issued for. If the VA replaces the
asset after the Discord notification went out, the snapshot no longer matches
the todo's current asset_path -- consume() reports that as "stale" rather than
approving the wrong file. This is the same race todos.mark_uploaded() already
guards against for the Drive-upload job.
"""

from __future__ import annotations

from functools import lru_cache

from ofmhelpers.config import settings
from ofmhelpers.web.db.repositories import ApprovalTokenRepository


@lru_cache(maxsize=1)
def _repository() -> ApprovalTokenRepository:
    """The process-wide approval-token repository, built on first use rather than at
    import. Lazy because constructing it binds a Redis connection for its
    cache, and at import time OFM_REDIS_URL may not be its final value yet."""
    return ApprovalTokenRepository()


def create_token(todo_id: str, asset_path: str) -> str:
    # Read the TTL fresh (not at import) so a test's monkeypatch is observed,
    # matching how config is read everywhere else.
    return _repository().create(
        todo_id, asset_path, settings.web.approval_token_ttl_seconds
    )


def get_token(token: str) -> dict | None:
    return _repository().get(token)


def consume(token: str, current_asset_path: str) -> str:
    """Validates and, only on success, marks the token used in the same
    read-modify-write pass. Returns "ok" / "not_found" / "expired" / "used" /
    "stale"."""
    return _repository().consume(token, current_asset_path)
