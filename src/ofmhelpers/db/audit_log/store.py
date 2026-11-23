# Copyright (C) 2026 jpkrajewski

from functools import lru_cache

from ofmhelpers.db.audit_log.repository import AuditLogRepository
lazy from ofmhelpers.db.audit_log.schema import AuditEntry

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
