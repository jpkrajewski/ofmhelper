from html.parser import HTMLParser

import pytest
from app_pages import ADMIN_HEADER, ADMIN_ONLY_PAGES, VA_PAGES
from fastapi.testclient import TestClient
from signin import sign_in

from ofmhelpers.background import sweeps
from ofmhelpers.web.main import app

PROTECTED_PAGES = [
    "/home",
    "/generate",
    "/download-assets",
    "/character-sheet",
    "/uniqify",
    "/clone",
    "/file-manager",
    "/action-log",
    "/cookies",
    "/self-links",
]


def _nav_paths(html: str) -> list[str]:

    class SiteNav(HTMLParser):
        inside = False
        paths: tuple[str, ...] = ()

        def handle_starttag(self, tag, attrs):
            found = dict(attrs)
            if tag == "nav" and found.get("id") == "site-nav":
                self.inside = True
            elif tag == "a" and self.inside:
                self.paths += (found["href"],)

        def handle_endtag(self, tag):
            if tag == "nav":
                self.inside = False

    parser = SiteNav()
    parser.feed(html)
    return list(parser.paths)


def test_admin_opens_every_page():
    client = TestClient(app)
    client.post("/login", data={"password": "test-admin", "next": "/"})

    for path in VA_PAGES + ADMIN_ONLY_PAGES:
        r = client.get(path)
        assert r.status_code == 200, path
        assert _nav_paths(r.text) == ADMIN_HEADER, path


@pytest.mark.parametrize(
    "path", ["/todo", "/helpers", "/models", "/competition", "/replicate", "/approve/x"]
)
def test_removed_pages_are_gone(path):
    client = TestClient(app)
    client.post("/login", data={"password": "test-admin", "next": "/"})
    assert client.get(path).status_code == 404


def test_app_starts_and_stops_cleanly():
    with TestClient(app) as client:
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}


def test_the_web_process_runs_no_sweep(monkeypatch):
    started = []

    async def loop(sweep: sweeps.Sweep) -> None:
        started.append(sweep.name)

    monkeypatch.setattr(sweeps, "_loop", loop)

    with TestClient(app) as client:
        assert client.get("/health").status_code == 200

    assert started == []


def test_health_login_and_landing_page_are_public():
    client = TestClient(app)
    assert client.get("/health").status_code == 200
    assert client.get("/login").status_code == 200
    assert client.get("/").status_code == 200


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


@pytest.mark.parametrize("path", ADMIN_ONLY_PAGES)
def test_admin_only_page_returns_403_for_va(path):
    client = TestClient(app)
    sign_in(client)
    assert client.get(path).status_code == 403


@pytest.mark.parametrize("path", ADMIN_ONLY_PAGES)
def test_admin_only_page_loads_for_admin(path):
    client = TestClient(app)
    client.post("/login", data={"password": "test-admin", "next": "/"})
    assert client.get(path).status_code == 200


def test_wrong_password_is_rejected():
    client = TestClient(app)
    r = client.post("/login", data={"password": "not-it", "next": "/"})
    assert r.status_code == 401


def test_va_password_also_logs_in():
    client = TestClient(app)
    sign_in(client)
    assert client.get("/generate").status_code == 200


def test_generation_js_disables_submit_button_with_a_cooldown():
    client = TestClient(app)
    js = client.get("/static/js/generation.js").text

    assert "SUBMIT_COOLDOWN_MS = 3000" in js
    assert "submitBtn.disabled = true" in js
    assert "submitBtn.disabled = false" in js
    assert "setTimeout" in js
