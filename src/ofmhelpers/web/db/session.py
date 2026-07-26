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

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from ofmhelpers.config import settings

if TYPE_CHECKING:
    from collections.abc import Iterator

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
        _engine = create_engine(url, pool_pre_ping=True, future=True)
        _engine_url = url
    return _engine


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
