"""Google Drives connected for /clone uploads."""

from __future__ import annotations

import time
import uuid

from ofmhelpers.web.db.models import DriveConnectionRow
from ofmhelpers.web.db.repositories.cached_repository import (
    CachedRepository,
    cached,
    invalidates_cache,
)
from ofmhelpers.web.db.session import session_scope


class DriveConnectionRepository(CachedRepository):
    cache_namespace = "drive_connection"

    @invalidates_cache
    def add(self, email: str, refresh_token: str) -> str:
        connection_id = uuid.uuid4().hex
        with session_scope() as s:
            s.add(
                DriveConnectionRow(
                    id=connection_id,
                    email=email,
                    refresh_token=refresh_token,
                    created_at=time.time(),
                )
            )
        return connection_id

    @cached
    def get(self, connection_id: str) -> dict | None:
        with session_scope() as s:
            row = s.get(DriveConnectionRow, connection_id)
            if row is None:
                return None
            return {
                "id": row.id,
                "email": row.email,
                "refresh_token": row.refresh_token,
            }

    @invalidates_cache
    def delete(self, connection_id: str) -> bool:
        with session_scope() as s:
            row = s.get(DriveConnectionRow, connection_id)
            if row is None:
                return False
            s.delete(row)
            return True
