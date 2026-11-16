"""Reads `audit_log` (alembic a1b2c3d4e5f8): a row's history, what someone
did, what happened to someone's data, newest first. Nothing writes here:
the database's triggers do. Nothing cached: it is read by people looking
into something, and it only grows."""

from sqlalchemy import select

from ofmhelpers.db.models import AuditLogRow
from ofmhelpers.db.session import session_scope
from ofmhelpers.schemas.audit import AuditEntry


def _entry(row: AuditLogRow) -> AuditEntry:
    return AuditEntry(
        id=row.id,
        created_at=row.created_at,
        table_name=row.table_name,
        row_id=row.row_id,
        op=row.op,
        owner_id=row.owner_id,
        actor_id=row.actor_id,
        viewing_id=row.viewing_id,
        via=row.via,
        ref=row.ref,
        trace_id=row.trace_id,
        changes=row.changes,
    )


class AuditLogRepository:
    def _newest(self, *where, limit: int) -> list[AuditEntry]:
        log = AuditLogRow
        with session_scope() as s:
            rows = s.execute(
                select(log).where(*where).order_by(log.id.desc()).limit(limit)
            ).scalars()
            return [_entry(r) for r in rows]

    def history(self, table: str, row_id: str, limit: int = 100) -> list[AuditEntry]:
        """Every change to one row (`row_id`: its key values joined by ":")."""
        log = AuditLogRow
        return self._newest(log.table_name == table, log.row_id == row_id, limit=limit)

    def by_actor(self, actor_id: int, limit: int = 100) -> list[AuditEntry]:
        """What `actor_id` did (as themselves or viewing as someone)."""
        return self._newest(AuditLogRow.actor_id == actor_id, limit=limit)

    def of_owner(self, owner_id: int, limit: int = 100) -> list[AuditEntry]:
        """What happened to `owner_id`'s rows, whoever did it."""
        return self._newest(AuditLogRow.owner_id == owner_id, limit=limit)
