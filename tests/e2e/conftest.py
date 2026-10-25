"""Fixtures for the end-to-end suite: a real Chromium driving the app.

Hermetic: the session starts its own app (uvicorn) and RQ worker in this
container, on the throwaway test database and Redis db the rest of the
suite uses, with every outside service faked (OFM_FAKE_EXTERNALS: Drive,
AI providers, Instagram) and in a fresh working directory, so no `.env`,
no dev data, no uploads of yours are ever read. Each test starts on empty
tables and creates what it needs (seeding through the stores is fine: the
test and the app share the database and the cache).

    scripts/test.sh e2e

On a failure the Allure report gets the page's screenshot, the Playwright
trace (`playwright show-trace`), its console errors, failed requests and
the app's log.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
from functools import lru_cache
from typing import TYPE_CHECKING

import allure
import pytest
import requests
from alembic.config import Config
from e2e_session import Session, log_in
from PIL import Image
from playwright.sync_api import Browser, sync_playwright
from pydantic import BaseModel
from repo_paths import REPO
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from alembic import command
from ofmhelpers.gdrive.fake import FakeDrive
from ofmhelpers.links.fake import BOUNCY_FAKE_KEY, OOPSIE_FAKE_KEY
from ofmhelpers.llm.gemini.fake import FAKE_KEY as GEMINI_FAKE_KEY

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

# Who each role signs in as, through the real login form: the admin
# password account, and a user with a password of their own.
ACCOUNTS = {
    "ADMIN": ("admin", "e2e-admin-password"),
    "VA": ("va@example.com", "e2e-va-password"),
}
_STARTUP_TIMEOUT_S = 60
_REPORTS = pytest.StashKey[dict]()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class E2EBackend(BaseModel):
    """The app's own database and Redis db: never the ones the integration
    tests of the same xdist worker truncate and flush, and its RQ worker
    never takes their jobs."""

    database_url: str
    redis_url: str


def _app_env(workdir: Path, fakes: Path, backend: E2EBackend) -> dict[str, str]:
    """The suite's own environment (vault key: the root conftest set it)
    plus the app's. Nothing from a .env file: the app runs in `workdir`,
    which has none."""
    return {
        **os.environ,
        "OFM_DATABASE_URL": backend.database_url,
        "OFM_REDIS_URL": backend.redis_url,
        "OFM_RQ_ASYNC": "true",
        "OFM_RQ_WORKERS": "2",
        "OFM_CLONE_LINKS_WORKERS": "2",
        "OFM_FAKE_EXTERNALS": "1",
        "OFM_FAKES_DIR": str(fakes),
        "OFM_UPLOADS_ROOT": str(workdir / "uploads"),
        "OFM_AIGEN_OUT_DIR": str(workdir / "out"),
        "OFM_KIEAI_OUT_DIR": str(workdir / "kieai_out"),
        "OFM_KIEAI_TASK_LOG": str(workdir / "kieai_out" / "tasks.jsonl"),
        "OFM_KIEAI_COMPLETIONS_LOG": str(workdir / "kieai_out" / "completions.jsonl"),
        "OFM_COOKIES_FILE": str(workdir / "cookies.txt"),
        "APP_PASSWORD_ADMIN": ACCOUNTS["ADMIN"][1],
        "SESSION_SECRET": "e2e-session-secret",
        "OFM_GDRIVE_SERVICE_ACCOUNT": "{}",
        # The developer's .env holds the Logfire write token: never send.
        "LOGFIRE_TOKEN": "",
        # Sweeps only when a test asks (the Sync button), never between.
        "OFM_GDRIVE_CHECK_INTERVAL_S": "86400",
        "OFM_LOG_ACCESS": "true",
        # The admin's link providers: the fakes' keys (links/fake.py).
        "OOPSIE_API_KEY_ADMIN": OOPSIE_FAKE_KEY,
        "BOUNCY_API_KEY_ADMIN": BOUNCY_FAKE_KEY,
        # /clone-links' AI check: the Gemini fake's key (llm/gemini/fake.py).
        "GEMINI_API_KEY": GEMINI_FAKE_KEY,
        # Both sign-in providers set up; under OFM_FAKE_EXTERNALS their
        # consent is /auth/fake-consent and their token endpoints fakes.
        "OFM_GOOGLE_CLIENT_ID": "e2e-google-client",
        "OFM_GOOGLE_CLIENT_SECRET": "e2e-google-secret",
        "OFM_APPLE_SERVICES_ID": "e2e.apple.services",
        "OFM_APPLE_TEAM_ID": "E2ETEAM001",
        "OFM_APPLE_KEY_ID": "E2EKEY0001",
        "OFM_APPLE_PRIVATE_KEY": _apple_key_pem(),
    }


def _apple_key_pem() -> str:
    """A throwaway Sign in with Apple key: the fake token endpoint never
    checks the client secret, but the app still signs one."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    key = ec.generate_private_key(ec.SECP256R1())
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


def _wait_healthy(url: str, procs: list[subprocess.Popen], log: Path) -> None:
    deadline = time.monotonic() + _STARTUP_TIMEOUT_S
    while time.monotonic() < deadline:
        for proc in procs:
            if proc.poll() is not None:
                pytest.fail(f"e2e app exited {proc.returncode}:\n{log.read_text()}")
        try:
            if requests.get(f"{url}/health", timeout=2).ok:
                return
        except requests.ConnectionError:
            pass
        time.sleep(0.2)
    pytest.fail(f"e2e app not healthy in {_STARTUP_TIMEOUT_S}s:\n{log.read_text()}")


@pytest.fixture(scope="session")
def app_dir(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("e2e-app")


@pytest.fixture(scope="session")
def fakes_dir(app_dir: Path) -> Path:
    return app_dir / "fakes"


@pytest.fixture(scope="session")
def app_log(app_dir: Path) -> Path:
    return app_dir / "app.log"


@pytest.fixture(scope="session")
def e2e_backend(_test_environment) -> E2EBackend:
    """A fresh database next to the suite's (`<test db>_e2e`), built by the
    migrations, and Redis db 15 (the suite's workers use 1..8)."""
    base = make_url(os.environ["OFM_DATABASE_URL"])
    url = base.set(database=f"{base.database}_e2e")
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{url.database}" WITH (FORCE)'))
        conn.execute(text(f'CREATE DATABASE "{url.database}"'))
    admin.dispose()
    backend = E2EBackend(
        database_url=url.render_as_string(hide_password=False),
        redis_url=os.environ["OFM_REDIS_URL"].rsplit("/", 1)[0] + "/15",
    )
    suite_url = os.environ["OFM_DATABASE_URL"]
    os.environ["OFM_DATABASE_URL"] = backend.database_url
    try:
        command.upgrade(Config(str(REPO / "alembic.ini")), "head")
    finally:
        os.environ["OFM_DATABASE_URL"] = suite_url
    return backend


@pytest.fixture(scope="session")
def base_url(
    e2e_backend: E2EBackend, app_dir: Path, fakes_dir: Path, app_log: Path
) -> Iterator[str]:
    """The app and its workers (jobs, /clone-links runs, previews), up for
    the whole session."""
    port = _free_port()
    url = f"http://127.0.0.1:{port}"
    env = _app_env(app_dir, fakes_dir, e2e_backend)
    commands = [
        [
            sys.executable,
            "-m",
            "uvicorn",
            "ofmhelpers.web.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        [sys.executable, "-m", "ofmhelpers.worker"],
        [sys.executable, "-m", "ofmhelpers.worker", "clone-links"],
        [sys.executable, "-m", "ofmhelpers.worker", "previews"],
    ]
    with app_log.open("w") as log:
        procs = [
            subprocess.Popen(cmd, cwd=app_dir, env=env, stdout=log, stderr=log)
            for cmd in commands
        ]
        try:
            _wait_healthy(url, procs, app_log)
            yield url
        finally:
            for proc in procs:
                proc.terminate()
            for proc in procs:
                try:
                    proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    proc.kill()


@pytest.fixture(autouse=True)
def _clean_tables(base_url, e2e_backend: E2EBackend, monkeypatch):
    """Overrides the root one: the test seeds and reads the app's database
    and Redis, starting from empty tables and caches -- but the RQ keys stay
    (flushing them would pull the registry from under the running worker)."""
    monkeypatch.setenv("OFM_DATABASE_URL", e2e_backend.database_url)
    monkeypatch.setenv("OFM_REDIS_URL", e2e_backend.redis_url)
    from ofmhelpers.cache import get_redis
    from ofmhelpers.web.db.models import Base
    from ofmhelpers.web.db.session import get_engine

    tables = ", ".join(Base.metadata.tables)
    with get_engine().begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    redis = get_redis()
    stale = [k for k in redis.scan_iter("*") if not k.startswith(b"rq:")]
    if stale:
        redis.delete(*stale)
    _seed_users(e2e_backend.database_url)


@lru_cache(maxsize=1)
def _va_password_hash() -> str:
    from ofmhelpers.signin.passwords import hash_password

    return hash_password(ACCOUNTS["VA"][1])


def _seed_users(database_url: str) -> None:
    """The signed-in roles' users with fixed ids (1: the password admin, 2:
    the user), so the session cookies saved once (`auth_states`) keep
    pointing at them after every truncate. Plain SQL: the test process's
    repositories cache in whichever Redis they first met, not the app's."""
    va_email, _password = ACCOUNTS["VA"]
    with create_engine(database_url).begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users (id, email, name, role, active, created_at,"
                " google_refresh_token, password_hash, is_password_account)"
                " VALUES"
                " (1, 'admin', 'Admin (password)', 'admin', true, 0, '', '', true),"
                " (2, :va, '', 'user', true, 0, '', :hash, false)"
                " ON CONFLICT (id) DO NOTHING"
            ),
            {"va": va_email, "hash": _va_password_hash()},
        )
        conn.execute(text("SELECT setval('users_id_seq', (SELECT max(id) FROM users))"))


@pytest.fixture
def jobs_idle(e2e_backend: E2EBackend) -> Callable[[], None]:
    """Waits until the app's RQ workers have nothing queued or running on
    any job queue: work the app starts by itself (e.g. the Drive sync on
    connecting Google) must be over before a test changes what it would
    see."""
    from redis import Redis
    from rq import Queue
    from rq.registry import StartedJobRegistry

    from ofmhelpers.cache import JOB_QUEUES

    def wait(timeout_s: float = 30) -> None:
        redis = Redis.from_url(e2e_backend.redis_url)
        pairs = [
            (Queue(name, connection=redis), StartedJobRegistry(name, connection=redis))
            for name in JOB_QUEUES
        ]
        deadline = time.monotonic() + timeout_s
        while any(queue.count or started.count for queue, started in pairs):
            assert time.monotonic() < deadline, "the app's jobs never finished"
            time.sleep(0.1)

    return wait


@pytest.fixture
def fake_drive(fakes_dir: Path, base_url) -> FakeDrive:
    """The app's Drive, emptied: each person's My Drive appears when their
    Google account first uses it (`FakePersonDrive`)."""
    (fakes_dir / "drive.json").unlink(missing_ok=True)
    return FakeDrive(fakes_dir)


@pytest.fixture(scope="session")
def browser() -> Iterator[Browser]:
    with sync_playwright() as pw:
        chromium = pw.chromium.launch()
        yield chromium
        chromium.close()


@pytest.fixture(scope="session")
def auth_states(
    browser: Browser, base_url: str, tmp_path_factory, e2e_backend: E2EBackend
) -> dict[str, Path]:
    """One login per role for the whole session, from users made in a fixed
    order (`_seed_users`; every test's truncate makes them again)."""
    root = tmp_path_factory.mktemp("e2e-auth")
    states = {}
    with create_engine(e2e_backend.database_url).begin() as conn:
        conn.execute(text("TRUNCATE users RESTART IDENTITY CASCADE"))
    _seed_users(e2e_backend.database_url)
    for role, (email, password) in ACCOUNTS.items():
        states[role] = root / f"{role.lower()}.json"
        log_in(browser, base_url, email, password, states[role])
    return states


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_makereport(item, call):
    report = yield
    item.stash.setdefault(_REPORTS, {})[report.when] = report
    return report


def _failed(request) -> bool:
    return any(r.failed for r in request.node.stash.get(_REPORTS, {}).values())


def _finish(
    session: Session, name: str, request, tmp_path: Path, app_log: Path
) -> None:
    """Closes the tab; on a failure attaches what explains it."""
    if not _failed(request):
        session.close(trace=None)
        return
    try:
        allure.attach(
            session.page.screenshot(full_page=True),
            name=f"{name}: screenshot",
            attachment_type=allure.attachment_type.PNG,
        )
    except Exception as exc:  # the page may be the thing that broke
        allure.attach(str(exc), name=f"{name}: screenshot failed")
    allure.attach(
        "\n".join(session.js_errors) or "(none)",
        name=f"{name}: console errors",
        attachment_type=allure.attachment_type.TEXT,
    )
    allure.attach(
        "\n".join(session.failed_requests) or "(none)",
        name=f"{name}: failed requests",
        attachment_type=allure.attachment_type.TEXT,
    )
    trace = tmp_path / f"{name}-trace.zip"
    session.close(trace=trace)
    allure.attach.file(trace, name=f"{name}: trace", extension="zip")
    allure.attach.file(
        app_log, name="app log", attachment_type=allure.attachment_type.TEXT
    )


def _session(
    role: str, name: str, browser, base_url, auth_states, request, tmp_path, app_log
):
    session = Session(browser, base_url, auth_states[role])
    yield session
    _finish(session, name, request, tmp_path, app_log)


@pytest.fixture
def admin(
    browser, base_url, auth_states, request, tmp_path, app_log
) -> Iterator[Session]:
    yield from _session(
        "ADMIN", "admin", browser, base_url, auth_states, request, tmp_path, app_log
    )


@pytest.fixture
def va(browser, base_url, auth_states, request, tmp_path, app_log) -> Iterator[Session]:
    yield from _session(
        "VA", "va", browser, base_url, auth_states, request, tmp_path, app_log
    )


@pytest.fixture
def coworker(
    browser, base_url, auth_states, request, tmp_path, app_log
) -> Iterator[Session]:
    """A second VA, in its own browser context -- another person's tab."""
    yield from _session(
        "VA", "coworker", browser, base_url, auth_states, request, tmp_path, app_log
    )


@pytest.fixture
def subject_images(tmp_path: Path) -> list[Path]:
    paths = []
    for name, colour in (("front.png", "crimson"), ("side.png", "navy")):
        path = tmp_path / name
        Image.new("RGB", (256, 256), colour).save(path)
        paths.append(path)
    return paths


@pytest.fixture
def source_videos(tmp_path: Path) -> list[Path]:
    """Two short real clips -- /clone probes each one's length with ffprobe."""
    assert shutil.which("ffmpeg"), "ffmpeg is part of the test image"
    paths = []
    for name, seconds in (("dance.mp4", 3), ("walk.mp4", 5)):
        path = tmp_path / name
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "testsrc=size=320x240:rate=25",
                "-t",
                str(seconds),
                "-pix_fmt",
                "yuv420p",
                str(path),
            ],
            check=True,
            timeout=60,
        )
        paths.append(path)
    return paths
