# Copyright (C) 2026 jpkrajewski

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class AuditEntry(BaseModel):
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
