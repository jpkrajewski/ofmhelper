from contextlib import contextmanager
from typing import Any
lazy from collections.abc import Iterator

import sqlalchemy
from sqlalchemy import Engine, event
from sqlalchemy.orm import Session, sessionmaker

from ofmhelpers import acting
from ofmhelpers.config import settings

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
    _parameters: object,
    _context: object,
    _executemany: object,
) -> None:
    if conn.info.get(_STAMPED) or not statement.lstrip()[:6].upper().startswith(
        _WRITES
    ):
        return
    conn.info[_STAMPED] = True
    who = acting.current()
    if who is not None:
        cursor.execute(
            _ACTING_SQL,
            {k: "" if v is None else str(v) for k, v in who.model_dump().items()},
        )


def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)


@contextmanager
def session_scope() -> Iterator[Session]:
    session = get_sessionmaker()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
