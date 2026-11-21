import importlib
import os
from pathlib import Path

os.environ["LOGFIRE_TOKEN"] = ""
os.environ["SENTRY_DSN"] = ""
os.environ["OFM_DEBUG"] = "0"
os.environ["OFM_ENV"] = "development"
os.environ["SESSION_SECRET"] = "test-secret"
os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ["OFM_EVENTS_SENDERS_OFF"] = ""
os.environ["OFM_RECOVERY_RESTARTS"] = "true"
os.environ["OFM_DRIVE_SWEEP"] = "true"
os.environ["OFM_LINKS_BACKGROUND_SYNC"] = "true"

import pytest
from cryptography.fernet import Fernet
from pydantic_settings import BaseSettings
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
lazy from alembic.config import Config

lazy from alembic import command
lazy from ofmhelpers.cache import get_redis
lazy from ofmhelpers.db.models import Base
lazy from ofmhelpers.db.session import get_engine
lazy from ofmhelpers.features.fakes import install_fakes
lazy from ofmhelpers.integrations.ai_providers.apiyi.fake import fake_apiyi
lazy from ofmhelpers.integrations.ai_providers.elyum.fake import fake_elyum
lazy from ofmhelpers.integrations.ai_providers.higgsfield.fake import fake_higgsfield
lazy from ofmhelpers.integrations.ai_providers.kie.fake import fake_kie
lazy from ofmhelpers.integrations.ai_providers.runninghub.fake import fake_runninghub
lazy from ofmhelpers.integrations.apify.fake import fake_apify
lazy from ofmhelpers.integrations.fanvue.fake import fake_fanvue
lazy from ofmhelpers.integrations.gdrive.fake import _fake_drive
lazy from ofmhelpers.integrations.instagram.fake import fake_instagram
lazy from ofmhelpers.integrations.llm.cerebras.fake import fake_cerebras
lazy from ofmhelpers.integrations.llm.gemini.fake import fake_gemini
lazy from ofmhelpers.integrations.llm.groq.fake import fake_groq
lazy from ofmhelpers.integrations.pinterest.fake import fake_pinterest
lazy from ofmhelpers.integrations.senders.telegram.client import _client as _telegram
lazy from ofmhelpers.integrations.senders.telegram.fake import fake_telegram
lazy from ofmhelpers.integrations.short_links.fake import _fake_bouncy, _fake_oopsie
lazy from ofmhelpers.integrations.signin import passwords
lazy from ofmhelpers.integrations.stacked.fake import fake_stacked
lazy from ofmhelpers.media import fake_media

settings_module = importlib.import_module("ofmhelpers.config.settings")

_TESTS_ROOT = Path(__file__).parent

_XDIST_WORKER = os.environ.get("PYTEST_XDIST_WORKER", "")
_WORKER_INDEX = int(_XDIST_WORKER.removeprefix("gw") or 0)
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
TEST_REDIS_URL = _per_worker_redis(
    os.environ.get("OFM_TEST_REDIS_URL", "redis://127.0.0.1:6379/1")
)


def _ensure_test_database(url: str) -> None:
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
    return TEST_DATABASE_URL


@pytest.fixture(scope="session", autouse=True)
def _test_environment(tmp_path_factory):
    os.environ["OFM_DATABASE_URL"] = TEST_DATABASE_URL
    os.environ["OFM_REDIS_URL"] = TEST_REDIS_URL
    os.environ["OFM_RQ_ASYNC"] = "false"
    os.environ["OFM_RATE_LIMIT_ENABLED"] = "false"
    os.environ["OFM_AIGEN_DOWNLOAD_RETRY_WAITS_S"] = "[0, 0]"
    os.environ["OFM_AIGEN_RATE_LIMIT_WAITS_S"] = "[0, 0, 0]"
    os.environ["OFM_VAULT_KEY"] = Fernet.generate_key().decode()
    run_dir = tmp_path_factory.mktemp("app-files")
    os.environ["OFM_UPLOADS_ROOT"] = str(run_dir / "uploads")
    os.environ["OFM_COOKIES_FILE"] = str(run_dir / "cookies" / "cookies.txt")


@pytest.fixture(scope="session")
def _test_database(_test_environment):
    _ensure_test_database(TEST_DATABASE_URL)

    with get_engine().begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    command.upgrade(Config(str(_TESTS_ROOT.parent / "alembic.ini")), "head")


@pytest.fixture(autouse=True)
def _clean_tables(request):
    if request.node.get_closest_marker("unit"):
        monkeypatch = request.getfixturevalue("monkeypatch")
        monkeypatch.setenv("OFM_DATABASE_URL", _NO_DATABASE)
        monkeypatch.setenv("OFM_REDIS_URL", _NO_REDIS)
        return
    wipe = request.getfixturevalue("_wipe_sql")

    with get_engine().begin() as conn:
        conn.exec_driver_sql(wipe)
    get_redis().flushdb()


@pytest.fixture(scope="session")
def _wipe_sql(_test_database) -> str:

    with get_engine().begin() as conn:
        found = conn.execute(
            text(
                "SELECT quote_ident(sequencename) FROM pg_sequences"
                " WHERE schemaname = current_schema()"
            )
        )
        sequences = [row[0] for row in found]
    deletes = "".join(
        f'DELETE FROM "{table}"; '  # noqa: S608
        for table in Base.metadata.tables
    )
    restarts = ", ".join(f"setval('{name}', 1, false)" for name in sequences)
    return (
        "SET LOCAL session_replication_role = replica; "
        + deletes
        + (f"SELECT {restarts}" if sequences else "")
    )


_NO_DATABASE = (
    "postgresql+psycopg://unit:unit@/none?host=/nonexistent-unit-tests-have-no-db"
)
_NO_REDIS = "unix:///nonexistent-unit-tests-have-no-redis.sock"

_KINDS = ("unit", "integration", "contract", "e2e")
_ON_CI = os.environ.get("CI") == "true"
_LOCAL_SKIP = pytest.mark.skip(reason="ci_only: runs on CI only (owner 2026-11-20)")


def _kind(item: pytest.Item) -> str:
    rel = Path(item.path).relative_to(_TESTS_ROOT)
    kind = rel.parts[0]
    if kind not in _KINDS:
        msg = f"{rel}: every test lives under tests/{{{','.join(_KINDS)}}}/"
        raise pytest.UsageError(msg)
    return kind


collect_ignore_glob = (
    importlib.import_module("ofmhelpers.config.retired").retired().tests
)


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config, items):
    for item in items:
        kind = _kind(item)
        item.add_marker(kind)
        if kind == "unit":
            item.add_marker(pytest.mark.disable_socket)
        if kind == "e2e":
            item.add_marker(pytest.mark.xdist_group("e2e"))
        if item.get_closest_marker("live"):
            item.add_marker(pytest.mark.enable_socket)
        if item.get_closest_marker("ci_only") and not _ON_CI:
            item.add_marker(_LOCAL_SKIP)


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "ci_only: slow and of little use locally; runs only where CI=true (GitHub Actions)",
    )


@pytest.fixture
def fake_externals(monkeypatch, tmp_path):

    caches = (
        fake_instagram,
        _fake_oopsie,
        _fake_bouncy,
        fake_kie,
        fake_higgsfield,
        _fake_drive,
        fake_gemini,
        fake_groq,
        fake_cerebras,
        fake_apify,
        fake_stacked,
        fake_fanvue,
        fake_runninghub,
        fake_elyum,
        fake_apiyi,
        fake_telegram,
        fake_pinterest,
        _telegram,
        fake_media.mp4,
    )
    monkeypatch.setenv("OFM_FAKE_EXTERNALS", "1")
    monkeypatch.setenv("OFM_FAKES_DIR", str(tmp_path / "fakes"))
    for cached in caches:
        cached.cache_clear()
    install_fakes()
    yield
    for cached in caches:
        cached.cache_clear()


_SETTINGS_CLASSES = tuple(
    cls
    for cls in vars(settings_module).values()
    if isinstance(cls, type)
    and issubclass(cls, BaseSettings)
    and cls.__module__ == settings_module.__name__
)


@pytest.fixture(autouse=True)
def _no_real_dotenv_in_tests(monkeypatch):
    for cls in _SETTINGS_CLASSES:
        monkeypatch.setitem(cls.model_config, "env_file", None)


@pytest.fixture(autouse=True)
def _provider_files_in_tmp(monkeypatch, tmp_path):
    root = tmp_path / "provider-files"
    defaults = {"OFM_AIGEN_OUT_DIR": root / "out"}
    for name, path in defaults.items():
        if name not in os.environ:
            monkeypatch.setenv(name, str(path))


@pytest.fixture(autouse=True)
def _cheap_password_hashing(request, monkeypatch):
    if request.node.get_closest_marker("real_password_hashing"):
        return

    monkeypatch.setattr(passwords, "_N", 2**4)
