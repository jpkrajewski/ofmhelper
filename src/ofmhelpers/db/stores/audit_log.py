"""Who did what to the rows people edit (`audit_log`, written by the
database's triggers: `db/repositories/audit_log.py`)."""

from functools import lru_cache

from ofmhelpers.db.repositories import AuditLogRepository
lazy from ofmhelpers.schemas.audit import AuditEntry

__all__ = ["by_actor", "history", "of_owner"]


@lru_cache(maxsize=1)
def _repository() -> AuditLogRepository:
    return AuditLogRepository()


def history(table: str, row_id: str, limit: int = 100) -> list[AuditEntry]:
    return _repository().history(table, row_id, limit)


def by_actor(actor_id: int, limit: int = 100) -> list[AuditEntry]:
    return _repository().by_actor(actor_id, limit)


def of_owner(owner_id: int, limit: int = 100) -> list[AuditEntry]:
    return _repository().of_owner(owner_id, limit)
