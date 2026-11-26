# Copyright (C) 2026 jpkrajewski

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class PhoneRevision(BaseModel):
    id: int
    created_at: datetime
    change: str
    phones: int
    accounts: int


class Restored(BaseModel):
    revision_id: int
    logins_back: list[int] = []


class PhoneState(BaseModel):
    user_id: int
    taken_at: datetime
    change: str
    snapshot: dict[str, Any]


class PhoneDump(BaseModel):
    taken_at: datetime
    newest: int
    states: list[PhoneState]


class DumpWritten(BaseModel):
    path: str | None
    revisions: int = 0
    owners: int = 0


class DumpLoaded(BaseModel):
    revisions: list[int]
