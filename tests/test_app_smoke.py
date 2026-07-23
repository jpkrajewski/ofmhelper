"""
"Does the app actually start, and does every page load" -- as real pytest,
not one-off verification scripts. Anyone (including future-me) can catch a
broken route or a lifespan crash by just running `pytest`, and it stays
checked on every future change instead of evaporating after one manual run.
"""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ["APP_PASSWORD_VA"] = "test-va"
os.environ.setdefault("SESSION_SECRET", "test-secret")

import pytest
from fastapi.testclient import TestClient

from ofmhelpers.web.main import app

PROTECTED_PAGES = [
    "/",
    "/generate",
    "/download-assets",
    "/helpers",
    "/helpers/elevenlabs",
    "/helpers/scraper",
    "/helpers/radio-comms",
    "/uploads-manager",
    "/jobs",
    "/cookies",
]


def test_app_starts_and_stops_cleanly():
    """`with TestClient(...)` is what actually drives the ASGI lifespan
    (startup/shutdown events) -- a bare TestClient(app) never runs it, so
    this is the only thing that would have caught a broken startup (e.g.
    the recovery sweeper failing to launch)."""
    with TestClient(app) as client:
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}


def test_health_and_login_are_public():
    client = TestClient(app)
    assert client.get("/health").status_code == 200
    assert client.get("/login").status_code == 200


@pytest.mark.parametrize("path", PROTECTED_PAGES)
def test_protected_page_redirects_when_logged_out(path):
    client = TestClient(app)
    r = client.get(path, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/login")


@pytest.mark.parametrize("path", PROTECTED_PAGES)
def test_protected_page_loads_once_logged_in(path):
    client = TestClient(app)
    client.post("/login", data={"password": "test-admin", "next": "/"})
    assert client.get(path).status_code == 200


def test_wrong_password_is_rejected():
    client = TestClient(app)
    r = client.post("/login", data={"password": "not-it", "next": "/"})
    assert r.status_code == 401


def test_va_password_also_logs_in():
    client = TestClient(app)
    client.post("/login", data={"password": "test-va", "next": "/"})
    assert client.get("/generate").status_code == 200


def test_generation_js_disables_submit_button_with_a_cooldown():
    """Static check on the shipped JS -- there's no browser/JS runtime in
    this test suite to actually click a button and watch a timer, so this
    locks in that the cooldown logic (disable on submit, a 3s constant,
    scheduled re-enable) is present and wired, guarding against it being
    silently removed later. It does not verify real click timing."""
    client = TestClient(app)
    js = client.get("/static/js/generation.js").text

    assert "SUBMIT_COOLDOWN_MS = 3000" in js
    assert "submitBtn.disabled = true" in js
    assert "submitBtn.disabled = false" in js
    assert "setTimeout" in js
