"""Fixtures for the end-to-end suite: a real Chromium driving a running app.

Needs the whole stack up (the app, its worker, Postgres, Redis) -- locally:

    docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d
    uv run playwright install chromium
    uv run pytest -m e2e

`OFM_E2E_BASE_URL` points it elsewhere (default the local stack). Passwords
come from `OFM_E2E_ADMIN_PASSWORD` / `OFM_E2E_VA_PASSWORD`, falling back to
`APP_PASSWORD_ADMIN` / `APP_PASSWORD_VA` in the repo-root `.env` -- the ones
the local stack itself was started with.

The suite writes real jobs and uploads into that stack's database and asset
store, like a person clicking through it would. Never point it at prod.
"""

import os
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
from dotenv import dotenv_values
from e2e_session import Session
from PIL import Image
from playwright.sync_api import Browser, sync_playwright


def _password(role: str) -> str:
    value = os.environ.get(f"OFM_E2E_{role}_PASSWORD") or dotenv_values(".env").get(
        f"APP_PASSWORD_{role}"
    )
    if not value:
        pytest.skip(f"no {role.lower()} password: set OFM_E2E_{role}_PASSWORD")
    return value.strip()


@pytest.fixture(scope="session")
def browser() -> Iterator[Browser]:
    with sync_playwright() as pw:
        chromium = pw.chromium.launch()
        yield chromium
        chromium.close()


@pytest.fixture
def admin(browser: Browser) -> Iterator[Session]:
    session = Session(browser, _password("ADMIN"))
    yield session
    session.page.context.close()


@pytest.fixture
def va(browser: Browser) -> Iterator[Session]:
    session = Session(browser, _password("VA"))
    yield session
    session.page.context.close()


@pytest.fixture
def coworker(browser: Browser) -> Iterator[Session]:
    """A second VA, in its own browser context -- another person's tab."""
    session = Session(browser, _password("VA"))
    yield session
    session.page.context.close()


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
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not installed")
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
