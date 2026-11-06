"""The database itself (not one table): how big it is. Not cached: read
when an admin opens /home."""

from __future__ import annotations

from sqlalchemy import func, select

from ofmhelpers.db.session import session_scope


class DatabaseRepository:
    def size_bytes(self) -> int:
        """Everything the app's database takes on disk (tables, indexes)."""
        with session_scope() as s:
            return int(
                s.execute(
                    select(func.pg_database_size(func.current_database()))
                ).scalar_one()
            )
