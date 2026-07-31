"""Single-use magic-link tokens for VA asset approval."""

from __future__ import annotations

import time

from sqlalchemy import delete

from ofmhelpers.web.db.models import (
    ApprovalTokenRow,
)
from ofmhelpers.web.db.repositories.cached_repository import (
    CachedRepository,
    cached,
    invalidates_cache,
)
from ofmhelpers.web.db.session import session_scope


def _token_to_dict(row: ApprovalTokenRow) -> dict:
    return {
        "token": row.token,
        "todo_id": row.todo_id,
        "asset_path": row.asset_path,
        "created_at": row.created_at,
        "expires_at": row.expires_at,
        "used_at": row.used_at,
    }


class ApprovalTokenRepository(CachedRepository):
    cache_namespace = "approval_token"

    def _purge_expired(self, session, now: float) -> None:
        session.execute(
            delete(ApprovalTokenRow).where(ApprovalTokenRow.expires_at < now)
        )

    @invalidates_cache
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

    @cached
    def get(self, token: str) -> dict | None:
        with session_scope() as s:
            row = s.get(ApprovalTokenRow, token)
            return _token_to_dict(row) if row is not None else None

    @invalidates_cache
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
