"""www. of APP_BASE_URL redirects to the one public host, so a sign-in's
state cookie and the session live where the OAuth callback lands (prod,
2026-10-22: a Google Drive connect started on www. failed on the apex
callback). Other hosts -- loopback, the /health probe -- are served."""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ.setdefault("SESSION_SECRET", "test-secret")

import pytest
from fastapi.testclient import TestClient

from ofmhelpers.web.main import app


@pytest.fixture(autouse=True)
def _base(monkeypatch):
    monkeypatch.setenv("APP_BASE_URL", "https://ascendglobal.cash/")


def _client(base_url: str) -> TestClient:
    return TestClient(app, base_url=base_url, follow_redirects=False)


def test_a_google_connect_started_on_www_moves_to_the_callbacks_host():
    r = _client("https://www.ascendglobal.cash").get(
        "/auth/google/start?link=1&next=/clone"
    )
    assert r.status_code == 301
    assert (
        r.headers["location"]
        == "https://ascendglobal.cash/auth/google/start?link=1&next=/clone"
    )
    assert "set-cookie" not in r.headers


def test_a_post_on_www_keeps_its_method():
    r = _client("https://www.ascendglobal.cash").post("/login", data={"password": "x"})
    assert r.status_code == 308
    assert r.headers["location"] == "https://ascendglobal.cash/login"


def test_the_canonical_host_is_served():
    r = _client("https://ascendglobal.cash").get("/login")
    assert r.status_code == 200


@pytest.mark.parametrize("base", ["http://127.0.0.1:8000", "http://172.18.0.7:8000"])
def test_other_hosts_like_the_health_probe_are_served(base):
    assert _client(base).get("/health").status_code == 200


def test_www_base_url_moves_the_bare_host_there(monkeypatch):
    monkeypatch.setenv("APP_BASE_URL", "https://www.ascendglobal.cash/")
    r = _client("https://ascendglobal.cash").get("/login")
    assert r.status_code == 301
    assert r.headers["location"] == "https://www.ascendglobal.cash/login"


def test_nothing_moves_without_a_base_url(monkeypatch):
    monkeypatch.setenv("APP_BASE_URL", "")
    assert _client("https://www.ascendglobal.cash").get("/login").status_code == 200
