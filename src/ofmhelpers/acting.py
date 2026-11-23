# Copyright (C) 2026 jpkrajewski

from contextlib import contextmanager
from contextvars import ContextVar
from enum import StrEnum
lazy from collections.abc import Iterator

from pydantic import BaseModel


class Via(StrEnum):
    REQUEST = "request"
    JOB = "job"
    SWEEP = "sweep"
    WORKER = "worker"
    MIGRATION = "migration"
    TELEGRAM = "telegram"


class Acting(BaseModel, frozen=True):
    via: Via
    actor_id: int | None = None
    viewing_id: int | None = None
    ref: str = ""
    trace_id: str = ""


_ACTING: ContextVar[Acting | None] = ContextVar("ofm_acting", default=None)


@contextmanager
def acting(who: Acting) -> Iterator[None]:
    token = _ACTING.set(who)
    try:
        yield
    finally:
        _ACTING.reset(token)


def current() -> Acting | None:
    return _ACTING.get()
