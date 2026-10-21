import os

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError

from ofmhelpers.config.settings import (
    AccountsSettings,
    AigenSettings,
    DownloadersSettings,
    GDriveSettings,
    HiggsfieldSettings,
    InfraSettings,
    InstagramSettings,
    KieAISettings,
    SessionSettings,
    VaultSettings,
    WebSettings,
)

# The durable stores (jobs, applications) are Postgres-backed now, so the
# test suite needs a running Postgres. Bring one up first with:
#     docker compose up -d postgres redis
# Tests run against a SEPARATE database (ofmhelpers_test) so they never touch
# dev/prod data, and every table is truncated between tests for isolation.
# Under pytest-xdist every worker (gw0, gw1, ...) gets its own database and
# Redis db, so per-test TRUNCATE/FLUSHDB never hits another worker's test.
_XDIST_WORKER = os.environ.get("PYTEST_XDIST_WORKER", "")
_WORKER_INDEX = int(_XDIST_WORKER.removeprefix("gw") or 0)
# Redis has dbs 0-15: dev uses 0, the suite 1 + worker index.
_MAX_WORKERS = 15


def _per_worker_database(url: str) -> str:
    if not _XDIST_WORKER:
        return url
    parsed = make_url(url)
    return parsed.set(database=f"{parsed.database}_{_XDIST_WORKER}").render_as_string(
        hide_password=False
    )


def _per_worker_redis(url: str) -> str:
    if _WORKER_INDEX >= _MAX_WORKERS:
        msg = f"at most {_MAX_WORKERS} xdist workers (Redis has 16 dbs)"
        raise RuntimeError(msg)
    base, _, db = url.rpartition("/")
    return f"{base}/{int(db) + _WORKER_INDEX}"


TEST_DATABASE_URL = _per_worker_database(
    os.environ.get(
        "OFM_TEST_DATABASE_URL",
        "postgresql+psycopg://ofmhelpers:ofmhelpers@127.0.0.1:5432/ofmhelpers_test",
    )
)
# RQ integration tests use a real broker; db 1 keeps them off dev's db 0.
TEST_REDIS_URL = _per_worker_redis(
    os.environ.get("OFM_TEST_REDIS_URL", "redis://127.0.0.1:6379/1")
)


def _ensure_test_database(url: str) -> None:
    """CREATE DATABASE <test db> if it doesn't exist yet, by connecting to the
    server's default `postgres` database with autocommit."""
    parsed = make_url(url)
    admin_url = parsed.set(database="postgres")
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :n"),
                {"n": parsed.database},
            ).scalar()
            if not exists:
                conn.execute(text(f'CREATE DATABASE "{parsed.database}"'))
    except OperationalError as exc:  # pragma: no cover - environment guard
        msg = (
            "Cannot reach Postgres for the test suite. Start it first with "
            "`docker compose up -d postgres redis` (or set OFM_TEST_DATABASE_URL "
            f"to a reachable instance). Original error: {exc}"
        )
        raise RuntimeError(msg) from exc
    finally:
        engine.dispose()


@pytest.fixture(scope="session")
def test_database_url() -> str:
    """The throwaway test database's URL; its server hosts any other
    throwaway database a test needs."""
    return TEST_DATABASE_URL


@pytest.fixture(scope="session", autouse=True)
def _test_database():
    """Point the whole app at the throwaway test database and rebuild the
    schema once per session."""
    _ensure_test_database(TEST_DATABASE_URL)
    # Set on the real environment (not monkeypatch): session-scoped, and it
    # must be visible before any app code builds its lazy engine / redis conn.
    os.environ["OFM_DATABASE_URL"] = TEST_DATABASE_URL
    os.environ["OFM_REDIS_URL"] = TEST_REDIS_URL
    # Run enqueued jobs inline (like the old BackgroundTasks) so TestClient
    # tests see the result immediately. test_worker_integration flips this back
    # to async to exercise the real worker.
    os.environ["OFM_RQ_ASYNC"] = "false"
    # Every test shares one client host ("testclient"), so the per-IP write
    # ceiling would eventually trip on unrelated tests. Off by default; the
    # rate-limit tests turn it back on explicitly.
    os.environ["OFM_RATE_LIMIT_ENABLED"] = "false"
    # A throwaway vault key: /phones logins encrypt with it (a test that
    # needs another key, or none, monkeypatches OFM_VAULT_KEY).
    os.environ["OFM_VAULT_KEY"] = Fernet.generate_key().decode()

    # Import here, after the env var is set, so the engine binds to the test DB.
    from ofmhelpers.web.db.models import Base
    from ofmhelpers.web.db.session import get_engine

    engine = get_engine()
    # Rebuilt every session: create_all alone never alters an existing
    # table, so a local test database would drift from the models.
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


@pytest.fixture(autouse=True)
def _clean_tables():
    """Truncate every mapped table before each test so nothing leaks between
    tests -- the DB equivalent of the old per-test temp JSON files. The list
    comes from the ORM metadata, so it matches what create_all built: a
    hardcoded list kept naming retired tables that a fresh CI database never
    has. Also flushes the test Redis db: repository.py caches reads there, and
    a cached list/get from a previous test would otherwise survive the
    TRUNCATE above until its TTL expired."""
    from ofmhelpers.cache import get_redis
    from ofmhelpers.web.db.models import Base
    from ofmhelpers.web.db.session import get_engine

    tables = ", ".join(Base.metadata.tables)
    with get_engine().begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    get_redis().flushdb()


_SETTINGS_CLASSES = (
    SessionSettings,
    WebSettings,
    InfraSettings,
    KieAISettings,
    AigenSettings,
    HiggsfieldSettings,
    DownloadersSettings,
    # Its .env holds the real scan proxies: a test must never go through them.
    InstagramSettings,
    AccountsSettings,
    VaultSettings,
    # Its .env holds the real Drive key: a test must never reach Drive.
    GDriveSettings,
)


@pytest.fixture(autouse=True)
def _no_real_dotenv_in_tests(monkeypatch):
    """Every settings group in ofmhelpers/config/settings.py has
    env_file=".env" for real app usage. Without this fixture, a test's
    monkeypatch.delenv("SOME_VAR") wouldn't actually make that var look
    unset -- pydantic-settings would still fall back to whatever real
    secret sits in the repo-root .env file (loaded relative to cwd, which
    is the repo root under `uv run pytest`), since env_file is a lower-
    priority source than the process environment, not something delenv
    can hide. That silently leaked real secrets into tests asserting
    "unset" behavior (e.g. test_recovery.py, test_discord_client.py).
    Blanking env_file for the whole test session makes monkeypatch fully
    authoritative, matching how a bare os.getenv() behaved before this
    module existed. config/settings.py's actual .env-loading behavior is
    covered directly by test_config_settings.py using an explicit
    _env_file=<temp file> override instead of the real one."""
    for cls in _SETTINGS_CLASSES:
        monkeypatch.setitem(cls.model_config, "env_file", None)


@pytest.fixture(autouse=True)
def _provider_files_in_tmp(monkeypatch, tmp_path):
    """Provider output and kie.ai's audit logs default to /app/... (the
    container's paths). Anything that builds a provider -- even with its
    calls stubbed -- creates those directories, which a Linux CI runner
    refuses (`Permission denied: '/app'`); Windows quietly made C:\\app.
    Every test gets its own instead. A test that sets one itself wins."""
    root = tmp_path / "provider-files"
    defaults = {
        "OFM_AIGEN_OUT_DIR": root / "out",
        "OFM_KIEAI_TASK_LOG": root / "tasks.jsonl",
        "OFM_KIEAI_COMPLETIONS_LOG": root / "completions.jsonl",
    }
    for name, path in defaults.items():
        if name not in os.environ:
            monkeypatch.setenv(name, str(path))
