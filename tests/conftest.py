import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError

from ofmhelpers.config.settings import (
    DiscordSettings,
    DownloadersSettings,
    GDriveSettings,
    InfraSettings,
    KieAISettings,
    ReelMachineSettings,
    RunpodSettings,
    SessionSettings,
    WebSettings,
)

# The three durable stores (jobs, todos, approval tokens) are Postgres-backed
# now, so the test suite needs a running Postgres. Bring one up first with:
#     docker compose up -d postgres redis
# Tests run against a SEPARATE database (ofmhelpers_test) so they never touch
# dev/prod data, and every table is truncated between tests for isolation.
TEST_DATABASE_URL = os.environ.get(
    "OFM_TEST_DATABASE_URL",
    "postgresql+psycopg://ofmhelpers:ofmhelpers@127.0.0.1:5432/ofmhelpers_test",
)
# RQ integration tests use a real broker; db 1 keeps them off dev's db 0.
TEST_REDIS_URL = os.environ.get("OFM_TEST_REDIS_URL", "redis://127.0.0.1:6379/1")


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


@pytest.fixture(scope="session", autouse=True)
def _test_database():
    """Point the whole app at the throwaway test database and create the
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

    # Import here, after the env var is set, so the engine binds to the test DB.
    from ofmhelpers.web.db.models import Base
    from ofmhelpers.web.db.session import get_engine

    engine = get_engine()
    Base.metadata.create_all(engine)


@pytest.fixture(autouse=True)
def _clean_tables():
    """Truncate the three stores before each test so nothing leaks between
    tests -- the DB equivalent of the old per-test temp JSON files. Also
    flushes the test Redis db: repository.py caches reads there, and a
    cached list/get from a previous test would otherwise survive the
    TRUNCATE above until its TTL expired."""
    from ofmhelpers.web.db.session import get_engine
    from ofmhelpers.web.queue import get_redis

    with get_engine().begin() as conn:
        conn.execute(
            text(
                "TRUNCATE jobs, todos, approval_tokens, models, instagram_accounts "
                "RESTART IDENTITY CASCADE"
            )
        )
    get_redis().flushdb()


_SETTINGS_CLASSES = (
    SessionSettings,
    WebSettings,
    InfraSettings,
    KieAISettings,
    DownloadersSettings,
    DiscordSettings,
    ReelMachineSettings,
    GDriveSettings,
    RunpodSettings,
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
