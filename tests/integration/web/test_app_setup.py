"""`create_app()` (web/main.py): what a deployment's settings turn on and off,
and the order a request passes the middleware."""

import os

os.environ.setdefault("SESSION_SECRET", "test-secret")

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from starlette.middleware.sessions import SessionMiddleware

from ofmhelpers.config import settings
from ofmhelpers.web.exceptions import MESSAGE
from ofmhelpers.web.main import create_app
from ofmhelpers.web.middleware import (
    AuthMiddleware,
    BridgeMiddleware,
    CanonicalHostMiddleware,
    CompressionMiddleware,
    WriteRateLimitMiddleware,
)

DOCS = {"/docs", "/redoc", "/openapi.json"}


def _paths() -> set[str]:
    return {getattr(route, "path", "") for route in create_app().routes}


def test_development_serves_the_docs():
    assert _paths() >= DOCS


def test_production_serves_no_docs(monkeypatch):
    monkeypatch.setenv("OFM_ENV", "production")
    monkeypatch.setenv("SESSION_SECRET", "a" * 64)

    assert not DOCS & _paths()


def test_the_docs_are_behind_sign_in():
    """App-level routes have no access area: the session gate is what keeps
    the schema from someone signed out."""
    client = TestClient(create_app(), follow_redirects=False)

    response = client.get("/docs")

    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


def test_debug_is_off_unless_asked(monkeypatch):
    assert create_app().debug is False

    monkeypatch.setenv("OFM_DEBUG", "1")

    assert create_app().debug is True


def test_production_refuses_debug(monkeypatch):
    monkeypatch.setenv("OFM_ENV", "production")
    monkeypatch.setenv("OFM_DEBUG", "1")

    with pytest.raises(ValidationError, match="OFM_DEBUG"):
        _ = settings.infra


def test_production_refuses_a_short_session_secret(monkeypatch):
    """Prod ran on a 6-character one until 2026-11-18."""
    monkeypatch.setenv("OFM_ENV", "production")
    monkeypatch.setenv("SESSION_SECRET", "short1")

    with pytest.raises(ValidationError, match="SESSION_SECRET"):
        _ = settings.session


def test_production_starts_with_a_long_session_secret(monkeypatch):
    monkeypatch.setenv("OFM_ENV", "production")
    monkeypatch.setenv("SESSION_SECRET", "a" * 64)

    assert settings.session.session_secret == "a" * 64


def test_the_app_says_which_release_it_is(monkeypatch):
    monkeypatch.setenv("OFM_RELEASE", "abc1234")

    assert create_app().version == "abc1234"


def test_a_request_passes_the_middleware_in_this_order():
    assert [m.cls for m in create_app().user_middleware] == [
        BridgeMiddleware,
        CanonicalHostMiddleware,
        CompressionMiddleware,
        SessionMiddleware,
        WriteRateLimitMiddleware,
        AuthMiddleware,
    ]


def test_a_crash_answers_the_plain_sentence_with_debug_off():
    app = create_app()

    @app.get("/auth/crash-for-the-test")
    def crash() -> None:
        msg = "boom"
        raise RuntimeError(msg)

    client = TestClient(app, raise_server_exceptions=False)

    response = client.get("/auth/crash-for-the-test")

    assert response.status_code == 500
    assert MESSAGE in response.text
    assert "RuntimeError" not in response.text
