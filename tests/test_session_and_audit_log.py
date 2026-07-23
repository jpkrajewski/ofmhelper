"""
Covers two related changes: the session cookie's max_age was cut from 14
days down to 5 hours (shared admin/VA passwords, kept short on purpose),
and every login/logout now gets recorded into the Action log so it's clear
who was actually using the tool and when -- not just what jobs they ran.
"""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ["APP_PASSWORD_VA"] = "test-va"
os.environ.setdefault("SESSION_SECRET", "test-secret")

import pytest
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from ofmhelpers.web.main import app
from ofmhelpers.web.jobs import JOBS


@pytest.fixture
def client_factory():
    """Each test needs precise control over its own login/logout sequence,
    unlike the other test files' `client` fixture which pre-logs-in -- so
    this hands back a fresh, NOT-yet-authenticated TestClient per call."""
    return lambda: TestClient(app)


def _session_middleware_kwargs():
    mw = next(m for m in app.user_middleware if m.cls is SessionMiddleware)
    return mw.kwargs


def test_session_max_age_is_five_hours_not_fourteen_days():
    assert _session_middleware_kwargs()["max_age"] == 5 * 60 * 60


def test_va_login_is_recorded_with_va_actor(client_factory):
    client = client_factory()
    client.post("/login", data={"password": "test-va", "next": "/"})

    login_events = [j for j in JOBS.values() if j["task"] == "login"]
    latest = max(login_events, key=lambda j: j["created_at"])
    assert latest["actor"] == "va"


def test_failed_login_does_not_record_a_login_event(client_factory):
    client = client_factory()
    before = len([j for j in JOBS.values() if j["task"] == "login"])

    r = client.post("/login", data={"password": "totally-wrong", "next": "/"})
    assert r.status_code == 401

    after = len([j for j in JOBS.values() if j["task"] == "login"])
    assert after == before, "a failed login attempt must not be logged as a login"


def test_logout_is_recorded_with_the_actor_that_was_logged_in(client_factory):
    client = client_factory()
    client.post("/login", data={"password": "test-va", "next": "/"})

    r = client.post("/logout", follow_redirects=False)
    assert r.status_code == 303

    logout_events = [j for j in JOBS.values() if j["task"] == "logout"]
    assert logout_events, "no logout event was recorded"
    latest = max(logout_events, key=lambda j: j["created_at"])
    assert (
        latest["actor"] == "va"
    ), "logout must capture the actor before the session is cleared"


def test_logout_while_unauthenticated_is_blocked_before_logging_anything(
    client_factory,
):
    """/logout isn't in AuthMiddleware's public-path allowlist, so hitting it
    with no session redirects to /login like any other protected route --
    the logout handler (and its log_event call) never even runs. Confirms
    that path doesn't spuriously record a logout with no one attached to it."""
    client = client_factory()
    before = len([j for j in JOBS.values() if j["task"] == "logout"])

    r = client.post("/logout", follow_redirects=False)

    assert r.status_code == 303
    assert r.headers["location"].startswith("/login")
    after = len([j for j in JOBS.values() if j["task"] == "logout"])
    assert after == before, "an unauthenticated /logout must not reach log_event at all"


def test_login_and_logout_appear_in_the_action_log_page(client_factory):
    client = client_factory()
    client.post("/login", data={"password": "test-admin", "next": "/"})
    client.post("/logout")

    # log back in to view the page (it's auth-gated like everything else)
    client.post("/login", data={"password": "test-admin", "next": "/"})
    html = client.get("/action-log").text

    assert "Action log" in html
    assert ">login<" in html
    assert ">logout<" in html
