"""Submissions of the public landing-page application form."""

from __future__ import annotations

import time
import uuid

from sqlalchemy import select

from ofmhelpers.web.db.models import ApplicationRow
from ofmhelpers.web.db.repositories.cached_repository import (
    CachedRepository,
    cached,
    invalidates_cache,
)
from ofmhelpers.web.db.session import session_scope


def _application_to_dict(row: ApplicationRow) -> dict:
    return {
        "id": row.id,
        "first_name": row.first_name,
        "last_name": row.last_name,
        "email": row.email,
        "phone": row.phone,
        "telegram_handle": row.telegram_handle,
        "instagram_handle": row.instagram_handle,
        "monthly_revenue": row.monthly_revenue,
        "experience_level": row.experience_level,
        "goals": row.goals,
        "created_at": row.created_at,
    }


class ApplicationRepository(CachedRepository):
    cache_namespace = "application"

    @invalidates_cache
    def add(self, fields: dict) -> dict:
        row = ApplicationRow(id=uuid.uuid4().hex[:8], created_at=time.time(), **fields)
        with session_scope() as s:
            s.add(row)
            s.flush()
            return _application_to_dict(row)

    @cached
    def list_all(self) -> list[dict]:
        """Newest first."""
        with session_scope() as s:
            rows = (
                s.execute(
                    select(ApplicationRow).order_by(ApplicationRow.created_at.desc())
                )
                .scalars()
                .all()
            )
            return [_application_to_dict(r) for r in rows]

    @invalidates_cache
    def delete(self, application_id: str) -> bool:
        with session_scope() as s:
            row = s.get(ApplicationRow, application_id)
            if row is None:
                return False
            s.delete(row)
            return True
