"""
ofmhelpers/web/stores/drive_connections.py

Google Drives connected through routers/drive.py. A browser session keeps
only the id; /clone jobs carry it and upload with the stored token.
"""

from __future__ import annotations

from functools import lru_cache

from ofmhelpers.web.db.repositories import DriveConnectionRepository
from ofmhelpers.web.schemas import DriveConnection


@lru_cache(maxsize=1)
def _repository() -> DriveConnectionRepository:
    """Built on first use rather than at import (constructing it binds a Redis
    connection for its cache)."""
    return DriveConnectionRepository()


def add_connection(email: str, refresh_token: str) -> str:
    return _repository().add(email, refresh_token)


def get_connection(connection_id: str) -> DriveConnection | None:
    row = _repository().get(connection_id)
    return None if row is None else DriveConnection.model_validate(row)


def delete_connection(connection_id: str) -> bool:
    return _repository().delete(connection_id)
