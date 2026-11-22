from sqlalchemy import ColumnElement, select

from ofmhelpers.db.audit_log.schema import AuditEntry
from ofmhelpers.db.models import AuditLogRow
from ofmhelpers.db.session import session_scope


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
    def _newest(self, where: list[ColumnElement[bool]], limit: int) -> list[AuditEntry]:
        log = AuditLogRow
        with session_scope() as s:
            rows = s.execute(
                select(log).where(*where).order_by(log.id.desc()).limit(limit)
            ).scalars()
            return [_entry(r) for r in rows]

    def history(self, table: str, row_id: str, limit: int = 100) -> list[AuditEntry]:
        log = AuditLogRow
        return self._newest(
            [log.table_name == table, log.row_id == row_id], limit=limit
        )

    def by_actor(self, actor_id: int, limit: int = 100) -> list[AuditEntry]:
        return self._newest([AuditLogRow.actor_id == actor_id], limit=limit)

    def of_owner(self, owner_id: int, limit: int = 100) -> list[AuditEntry]:
        return self._newest([AuditLogRow.owner_id == owner_id], limit=limit)
