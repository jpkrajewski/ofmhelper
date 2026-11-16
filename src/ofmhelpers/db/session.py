"""
Lazy SQLAlchemy engine + session factory, built from
`settings.infra.database_url`.

Lazy on purpose (never at import time): the API and the RQ worker both import
this, and tests point OFM_DATABASE_URL at a throwaway DB inside a fixture --
if the engine were built at import we'd bind to whatever URL happened to be
set when the module first loaded. The engine is cached per URL, so production
(one URL) reuses a single pooled engine while a test that switches URLs gets a
fresh one.
"""

from contextlib import contextmanager
from typing import Any
lazy from collections.abc import Iterator

import sqlalchemy
from sqlalchemy import Engine, event
from sqlalchemy.orm import Session, sessionmaker

from ofmhelpers.config import settings
from ofmhelpers.db import acting

# Who acts, for the triggers (alembic a1b2c3d4e5f8): set for the transaction
# only (`set_config(..., true)`), so a pooled connection carries nothing over.
_ACTING_SQL = (
    "SELECT set_config('ofm.actor_id', %(actor_id)s, true), "
    "set_config('ofm.viewing_id', %(viewing_id)s, true), "
    "set_config('ofm.via', %(via)s, true), "
    "set_config('ofm.ref', %(ref)s, true), "
    "set_config('ofm.trace_id', %(trace_id)s, true)"
)
_WRITES = ("INSERT", "UPDATE", "DELETE", "WITH")
_STAMPED = "ofm_acting_stamped"

_engine: Engine | None = None
_engine_url: str | None = None


def get_engine() -> Engine:
    global _engine, _engine_url
    url = settings.infra.database_url
    if _engine is None or url != _engine_url:
        if _engine is not None:
            _engine.dispose()
        # pool_pre_ping: a long-idle worker's pooled connection may have been
        # dropped by Postgres/network; ping-and-recycle instead of erroring.
        # Looked up on the module at call time, not imported by name: the
        # telemetry instrumentation (telemetry.py) wraps
        # `sqlalchemy.create_engine` after this module is imported, and only
        # an engine made through the wrapper gets a span per query.
        # hide_parameters: a failed statement's message is logged, and the values
        # are fan messages, tokens and passwords.
        _engine = sqlalchemy.create_engine(
            url, pool_pre_ping=True, future=True, hide_parameters=True
        )
        event.listen(_engine, "begin", _new_transaction)
        event.listen(_engine, "before_cursor_execute", _say_who_acts)
        _engine_url = url
    return _engine


def _new_transaction(conn: sqlalchemy.Connection) -> None:
    conn.info.pop(_STAMPED, None)


def _say_who_acts(
    conn: sqlalchemy.Connection,
    cursor: Any,
    statement: str,
    *_args: object,
) -> None:
    """Before a transaction's first write, who acts goes to Postgres (one
    statement on the same cursor, no span of its own); a transaction that
    only reads pays nothing. Nobody set (a script, a test): nothing is
    sent, and the log names the client (`sql:<application_name>`)."""
    if conn.info.get(_STAMPED) or not statement.lstrip()[:6].upper().startswith(
        _WRITES
    ):
        return
    conn.info[_STAMPED] = True
    who = acting.current()
    if who is not None:
        # Every value as text, "" for none (the triggers read "" as unset).
        cursor.execute(
            _ACTING_SQL,
            {k: "" if v is None else str(v) for k, v in who.model_dump().items()},
        )


def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Commit on success, roll back on error, always close. The unit of work
    the repository layer runs each operation in."""
    session = get_sessionmaker()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
