"""AI checks (`clone_ai_checks`). Uncached: each row is written once and read
rarely (debugging, a card's verdict)."""

from __future__ import annotations

import time
from typing import Any

from sqlalchemy import select

from ofmhelpers.web.db.models import CloneAiCheckRow
from ofmhelpers.web.db.repositories.cached_repository import CachedRepository
from ofmhelpers.web.db.session import session_scope

_COLUMNS = [c.name for c in CloneAiCheckRow.__table__.columns]


def _to_dict(row: CloneAiCheckRow) -> dict[str, Any]:
    return {name: getattr(row, name) for name in _COLUMNS}


class CloneAiCheckRepository(CachedRepository):
    cache_namespace = "clone_ai_check"

    def add(self, values: dict[str, Any]) -> dict[str, Any]:
        """The new row, stamped now."""
        with session_scope() as s:
            row = CloneAiCheckRow(**values, created_at=time.time())
            s.add(row)
            s.flush()
            return _to_dict(row)

    def get(self, check_id: int) -> dict[str, Any] | None:
        with session_scope() as s:
            row = s.get(CloneAiCheckRow, check_id)
            return _to_dict(row) if row else None

    def for_jobs(self, job_ids: list[str]) -> list[dict[str, Any]]:
        """Every check of these jobs' results, oldest first."""
        if not job_ids:
            return []
        with session_scope() as s:
            rows = s.execute(
                select(CloneAiCheckRow)
                .where(CloneAiCheckRow.job_id.in_(job_ids))
                .order_by(CloneAiCheckRow.id)
            ).scalars()
            return [_to_dict(r) for r in rows]
