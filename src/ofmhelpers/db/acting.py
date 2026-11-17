"""Who is acting right now: the person a request, a job or a sweep works
for (and the admin behind "View as"), handed to Postgres with every write
transaction (`db/session.py`), where the triggers of alembic a1b2c3d4e5f8
stamp the row (`created_by` / `updated_by`) and write `audit_log`.

Set at the edges, read nowhere else: the auth middleware (a request), the
job runner (a job), the sweeps (a round), the worker and Alembic (their
own work). A ContextVar: each request, thread and job has its own, and a
thread pool or task started inside inherits it."""

from contextlib import contextmanager
from contextvars import ContextVar
from enum import StrEnum
lazy from collections.abc import Iterator

from pydantic import BaseModel


class Via(StrEnum):
    """Where an action came from (`audit_log.via`)."""

    REQUEST = "request"
    JOB = "job"
    SWEEP = "sweep"
    WORKER = "worker"
    MIGRATION = "migration"
    TELEGRAM = "telegram"


class Acting(BaseModel, frozen=True):
    """`actor_id` who acts (None: the system); `viewing_id` whom an admin is
    viewing as; `ref` what ("POST /phones/3", "trials 1a2b3c4d"); `trace_id`
    the Logfire trace of it."""

    via: Via
    actor_id: int | None = None
    viewing_id: int | None = None
    ref: str = ""
    trace_id: str = ""


_ACTING: ContextVar[Acting | None] = ContextVar("ofm_acting", default=None)


@contextmanager
def acting(who: Acting) -> Iterator[None]:
    """`who` acts for every write inside."""
    token = _ACTING.set(who)
    try:
        yield
    finally:
        _ACTING.reset(token)


def current() -> Acting | None:
    return _ACTING.get()
