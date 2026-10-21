"""Every Alembic revision on an empty database, on every run: upgrade to
head, downgrade to base, upgrade again, through `alembic.command` exactly as
the `migrate` service runs it; head matches the models. (The same round trip
on a copy of production's data is `-m snapshot`, test_prod_snapshot.py.)"""

import logging
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.engine import URL, make_url

from alembic import command
from ofmhelpers.web.db.models import Base

ROOT = Path(__file__).parents[2]


def _alembic() -> Config:
    return Config(str(ROOT / "alembic.ini"))


@pytest.fixture
def empty_url(monkeypatch, test_database_url) -> Iterator[URL]:
    base = make_url(test_database_url)
    url = base.set(database=f"{base.database}_migrations")
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{url.database}" WITH (FORCE)'))
        conn.execute(text(f'CREATE DATABASE "{url.database}"'))
    monkeypatch.setenv("OFM_DATABASE_URL", url.render_as_string(hide_password=False))
    yield url
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{url.database}" WITH (FORCE)'))
    admin.dispose()


@pytest.fixture
def db(empty_url) -> Iterator[Engine]:
    engine = create_engine(empty_url)
    yield engine
    engine.dispose()


def _revision(db: Engine) -> str | None:
    with db.connect() as conn:
        if not inspect(conn).has_table("alembic_version"):
            return None
        return conn.execute(text("SELECT version_num FROM alembic_version")).scalar()


def _tables(db: Engine) -> set[str]:
    with db.connect() as conn:
        return set(inspect(conn).get_table_names()) - {"alembic_version"}


def test_upgrade_downgrade_upgrade_on_an_empty_database(db):
    cfg = _alembic()
    head = ScriptDirectory.from_config(cfg).get_current_head()

    command.upgrade(cfg, "head")
    assert _revision(db) == head
    tables = _tables(db)

    command.downgrade(cfg, "base")
    assert _revision(db) is None
    assert _tables(db) == set()

    command.upgrade(cfg, "head")
    assert _revision(db) == head
    assert _tables(db) == tables


def test_head_has_every_table_the_models_define(db):
    command.upgrade(_alembic(), "head")
    assert set(Base.metadata.tables) <= _tables(db)


class _Records(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.INFO)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


def test_migrating_in_process_leaves_the_apps_loggers_on(db):
    """alembic/env.py's fileConfig used to disable every existing logger,
    silencing the app for the rest of the process (found as a /phones log
    assertion failing after the migration tests under xdist)."""
    app_logger = logging.getLogger("ofmhelpers.web.routers.inventory.phones")
    command.upgrade(_alembic(), "head")
    assert not app_logger.disabled
    records = _Records()
    app_logger.addHandler(records)
    try:
        app_logger.warning("still heard")
    finally:
        app_logger.removeHandler(records)
    assert records.messages == ["still heard"]
