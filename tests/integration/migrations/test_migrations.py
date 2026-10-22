"""Every Alembic revision on an empty database, on every run: upgrade to
head, downgrade to base, upgrade again, through `alembic.command` exactly as
the `migrate` service runs it; head matches the models. (The same round trip
on a copy of production's data is `-m snapshot`, test_prod_snapshot.py.)"""

import logging
from collections.abc import Iterator

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from repo_paths import REPO
from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.engine import URL, make_url

from alembic import command
from ofmhelpers.web.db.encrypted import decrypt
from ofmhelpers.web.db.models import Base


def _alembic() -> Config:
    return Config(str(REPO / "alembic.ini"))


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


def test_phone_logins_already_saved_are_tracked_once(db):
    """f4a5b6c7d8e9: Instagram logins on active phones become /accounts
    accounts (handle as Instagram writes it); a retired phone's do not, an
    account removed by hand stays removed, other services track nothing.
    Seeded in SQL: today's stores speak today's schema, not that revision's."""
    cfg = _alembic()
    command.upgrade(cfg, "e3f4a5b6c7d8")

    with db.begin() as conn:

        def phone(label: str, *, retired: bool = False) -> int:
            return conn.execute(
                text(
                    "INSERT INTO devices (label, model_id, custom_model, owner,"
                    " phone_number, notes, added_at, updated_at, retired_at)"
                    " VALUES (:label, 'iphone-13', '', '', '', '', 0, 0, :retired)"
                    " RETURNING id"
                ),
                {"label": label, "retired": 1.0 if retired else None},
            ).scalar_one()

        def login(device: int, service: str, username: str) -> None:
            login_id = conn.execute(
                text(
                    "INSERT INTO logins (service, provider, username, password,"
                    " backup_codes, notes, added_at, updated_at)"
                    " VALUES (:service, '', :username, '', '', '', 0, 0) RETURNING id"
                ),
                {"service": service, "username": username},
            ).scalar_one()
            conn.execute(
                text(
                    "INSERT INTO device_logins (device_id, login_id, linked_at)"
                    " VALUES (:device, :login, 0)"
                ),
                {"device": device, "login": login_id},
            )

        active, retired = phone("Active"), phone("Retired", retired=True)
        for service, username in (("instagram", "@Dany.Walking"), ("gmail", "a@b.c")):
            login(active, service, username)
        login(active, "instagram", "removed_one")
        login(retired, "instagram", "old_phone")
        conn.execute(
            text(
                "INSERT INTO tracked_accounts (platform, handle, notes, added_at,"
                " untracked_at) VALUES ('instagram', 'removed_one', '', 0, 1)"
            )
        )

    command.upgrade(cfg, "head")

    with db.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT handle, untracked_at IS NOT NULL FROM tracked_accounts"
                " ORDER BY handle"
            )
        ).all()
    assert rows == [("dany.walking", False), ("removed_one", True)]


def test_posted_files_belong_to_the_folder_their_login_has_now(db):
    """a1c2e3f4b5d6: posted files already recorded are tagged with their
    login's account folder (on prod: the one they were seen in); a login
    without a folder leaves them untagged, so they never count."""
    cfg = _alembic()
    command.upgrade(cfg, "f5a6b7c8d9e0")

    with db.begin() as conn:

        def login(username: str) -> int:
            return conn.execute(
                text(
                    "INSERT INTO logins (user_id, service, provider, username,"
                    " password, backup_codes, notes, added_at, updated_at)"
                    " VALUES (1, 'instagram', '', :username, '', '', '', 0, 0)"
                    " RETURNING id"
                ),
                {"username": username},
            ).scalar_one()

        with_folder, without = login("ann"), login("bob")
        conn.execute(
            text(
                "INSERT INTO drive_account_folders (login_id, folder_id, name,"
                " updated_at) VALUES (:login, 'live-folder', 'ann - Anna', 0)"
            ),
            {"login": with_folder},
        )
        for file_id, login_id in (("f1", with_folder), ("f2", without)):
            conn.execute(
                text(
                    "INSERT INTO drive_posted_files (file_id, login_id, name, kind,"
                    " first_seen_at) VALUES (:file, :login, 'v.mp4', 'video', 0)"
                ),
                {"file": file_id, "login": login_id},
            )

    command.upgrade(cfg, "head")

    with db.connect() as conn:
        rows = conn.execute(
            text("SELECT file_id, folder_id FROM drive_posted_files ORDER BY file_id")
        ).all()
    assert rows == [("f1", "live-folder"), ("f2", None)]


def test_the_shared_cookies_file_becomes_the_password_admins_own(
    db, monkeypatch, tmp_path
):
    """b1c2d3e4f5a7: the old agency-wide cookies.txt is stored, encrypted,
    as the password admin's cookies; the file stays; running it again
    (downgrade + upgrade) stores it once, and a user's cookies saved since
    are never overwritten."""
    shared = tmp_path / "cookies.txt"
    jar = "# Netscape HTTP Cookie File\n.instagram.com\tTRUE\t/\tTRUE\t0\tsessionid\tagency\n"
    shared.write_text(jar, encoding="utf-8")
    monkeypatch.setenv("OFM_COOKIES_FILE", str(shared))
    cfg = _alembic()
    command.upgrade(cfg, "a1c2e3f4b5d6")

    command.upgrade(cfg, "head")

    def stored() -> list[tuple[int, str, str]]:
        with db.connect() as conn:
            return [
                tuple(r)
                for r in conn.execute(
                    text(
                        "SELECT c.user_id, c.cookies, c.file_name FROM user_cookies c"
                        " JOIN users u ON u.id = c.user_id AND u.is_password_account"
                    )
                ).all()
            ]

    ((_admin, token, name),) = stored()
    assert name == "cookies.txt"
    assert token != jar
    assert "agency" not in token
    assert decrypt(token) == jar
    assert shared.read_text(encoding="utf-8") == jar

    command.downgrade(cfg, "a1c2e3f4b5d6")
    command.upgrade(cfg, "head")
    assert len(stored()) == 1


def test_without_a_shared_cookies_file_nobody_gets_cookies(db, monkeypatch, tmp_path):
    monkeypatch.setenv("OFM_COOKIES_FILE", str(tmp_path / "missing.txt"))
    command.upgrade(_alembic(), "head")
    with db.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM user_cookies")).scalar() == 0
