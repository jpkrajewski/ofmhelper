from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, JsonValue


class Outcome(StrEnum):
    NEW = "new"
    REPLAY = "replay"
    BUSY = "busy"
    MISMATCH = "mismatch"


class Asked(BaseModel, frozen=True):
    user_id: int
    key: str
    path: str
    fingerprint: str


class Held(BaseModel, frozen=True):
    asked: Asked
    claimed_at: datetime


class Claimed(BaseModel, frozen=True):
    outcome: Outcome
    held: Held | None = None
    response_status: int | None = None
    response_body: JsonValue = None
