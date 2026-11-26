# Copyright (C) 2026 jpkrajewski

from sqlalchemy import func, select

from ofmhelpers.db.session import session_scope


class DatabaseRepository:
    def size_bytes(self) -> int:
        with session_scope() as s:
            return int(
                s.execute(
                    select(func.pg_database_size(func.current_database())),
                ).scalar_one(),
            )
