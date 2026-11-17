"""One change from `audit_log` (alembic a1b2c3d4e5f8), as the CLI and an
agent read it."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class AuditEntry(BaseModel):
    """`changes`: an UPDATE's `{column: [old, new]}`, an INSERT's or
    DELETE's whole row (secrets as "changed" / "set"). `trace_id` opens the
    request or job in Logfire."""

    id: int
    created_at: datetime
    table_name: str
    row_id: str
    op: str
    owner_id: int | None
    actor_id: int | None
    viewing_id: int | None
    via: str
    ref: str
    trace_id: str | None
    changes: dict[str, Any]
