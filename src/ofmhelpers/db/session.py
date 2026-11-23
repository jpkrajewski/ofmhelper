# Copyright (C) 2026 jpkrajewski

from contextlib import contextmanager
from functools import lru_cache
from threading import Lock
from typing import Any
lazy from collections.abc import Iterator, Mapping, Sequence

import sqlalchemy
from sqlalchemy import Engine, event
from sqlalchemy.orm import Session, sessionmaker
lazy from sqlalchemy.engine.interfaces import DBAPICursor, ExecutionContext

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
_manager_lock = Lock()


class _EngineManager:
    def __init__(self) -> None:
        self._lock = Lock()
        self._engine: Engine | None = None
        self._url: str | None = None

    def get(self, url: str) -> Engine:
        with self._lock:
            if self._engine is not None and url == self._url:
                return self._engine
            engine = sqlalchemy.create_engine(
                url,
                pool_pre_ping=True,
                future=True,
                hide_parameters=True,
            )
            event.listen(engine, "begin", _new_transaction)
            event.listen(engine, "before_cursor_execute", _say_who_acts)
            previous = self._engine
            self._engine = engine
            self._url = url
            if previous is not None:
                previous.dispose()
            return engine


@lru_cache(maxsize=1)
def _engine_manager() -> _EngineManager:
    return _EngineManager()


def _get_engine_manager() -> _EngineManager:
    with _manager_lock:
        return _engine_manager()


def get_engine() -> Engine:
    return _get_engine_manager().get(settings.infra.database_url)


def _new_transaction(conn: sqlalchemy.Connection) -> None:
    conn.info.pop(_STAMPED, None)


def _say_who_acts(
    conn: sqlalchemy.Connection,
    cursor: DBAPICursor,
    statement: str,
    _parameters: Mapping[str, Any] | Sequence[Any],
    _context: ExecutionContext,
    _executemany: bool,  # noqa: FBT001
) -> None:
    if conn.info.get(_STAMPED) or not statement.lstrip()[:6].upper().startswith(
        _WRITES,
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
