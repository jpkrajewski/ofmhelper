import re
from html.parser import HTMLParser

import pytest
from app_pages import ADMIN_HEADER
from fastapi.testclient import TestClient
from pydantic import ValidationError
from signin import sign_in_agency
from starlette.middleware.sessions import SessionMiddleware

from ofmhelpers.config import settings
from ofmhelpers.web.exceptions import MESSAGE
from ofmhelpers.web.main import create_app
from ofmhelpers.web.middleware import (
    AuthMiddleware,
    BridgeMiddleware,
    CanonicalHostMiddleware,
    CompressionMiddleware,
    FrameGuardMiddleware,
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
    monkeypatch.setenv("SESSION_HTTPS_ONLY", "true")

    assert not DOCS & _paths()


def test_the_docs_are_behind_sign_in():
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
    monkeypatch.setenv("OFM_ENV", "production")
    monkeypatch.setenv("SESSION_SECRET", "short1")

    with pytest.raises(ValidationError, match="SESSION_SECRET"):
        _ = settings.session


def test_production_starts_with_a_long_session_secret(monkeypatch):
    monkeypatch.setenv("OFM_ENV", "production")
    monkeypatch.setenv("SESSION_SECRET", "a" * 64)
    monkeypatch.setenv("SESSION_HTTPS_ONLY", "true")

    assert settings.session.session_secret == "a" * 64


def test_production_refuses_session_cookies_that_may_go_over_http(monkeypatch):
    monkeypatch.setenv("OFM_ENV", "production")
    monkeypatch.setenv("SESSION_SECRET", "a" * 64)
    monkeypatch.setenv("SESSION_HTTPS_ONLY", "false")

    with pytest.raises(ValidationError, match="SESSION_HTTPS_ONLY"):
        _ = settings.session


def test_no_answer_of_the_app_may_be_framed_by_another_site():
    client = TestClient(create_app())

    for path in ("/login", "/home", "/static/css/tokens.css"):
        response = client.get(path, follow_redirects=False)
        assert response.headers["x-frame-options"] == "DENY", path
        assert (
            "frame-ancestors 'none'" in (response.headers["content-security-policy"])
        ), path


def test_the_app_says_which_release_it_is(monkeypatch):
    monkeypatch.setenv("OFM_RELEASE", "abc1234")

    assert create_app().version == "abc1234"


def test_a_request_passes_the_middleware_in_this_order():
    assert [m.cls for m in create_app().user_middleware] == [
        FrameGuardMiddleware,
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


class _Scripts(HTMLParser):
    def __init__(self, page: str) -> None:
        super().__init__()
        self.inline_nonces: list[str | None] = []
        self.handlers: list[str] = []
        self.feed(page)
        self.close()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        named: dict[str, str | None] = {}
        for name, value in attrs:
            named.setdefault(name, value)
        self.handlers += [name for name in named if name.startswith("on")]
        data = (named.get("type") or "").lower() == "application/json"
        if tag == "script" and "src" not in named and not data:
            self.inline_nonces.append(named.get("nonce"))


def _nonce_of(response) -> str:
    policy = response.headers["content-security-policy"]
    found = re.search(r"script-src 'self' 'nonce-([\w-]+)'", policy)
    assert found, policy
    assert "object-src 'none'" in policy
    assert "unsafe-inline" not in policy
    return found.group(1)


def test_the_script_check_reads_tags_and_attributes_in_any_spelling():
    found = _Scripts(
        "<SCRIPT>a()</SCRIPT>"
        '<Script NONCE="n" title="a>b">b()</Script>'
        '<script data-src="x">c()</script>'
        '<script src = "/x.js"></script>'
        "<script type='application/json'>{}</script>"
        '<img src="x" onerror = "d()">'
        '<script nonce="first" nonce="second">e()</script>'
    )

    assert found.inline_nonces == [None, "n", None, "first"]
    assert found.handlers == ["onerror"]


def test_every_page_runs_only_scripts_that_carry_its_own_nonce():
    client = TestClient(create_app())
    sign_in_agency(client)
    nonces = set()
    inline = 0

    for path in ADMIN_HEADER:
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 200, path
        nonce = _nonce_of(response)
        nonces.add(nonce)
        scripts = _Scripts(response.text)
        inline += len(scripts.inline_nonces)
        assert set(scripts.inline_nonces) <= {nonce}, path
        assert scripts.handlers == [], path
    assert len(nonces) == len(ADMIN_HEADER)
    assert inline > 0


def test_the_policy_names_a_new_nonce_for_every_request():
    client = TestClient(create_app())

    assert _nonce_of(client.get("/login")) != _nonce_of(client.get("/login"))
