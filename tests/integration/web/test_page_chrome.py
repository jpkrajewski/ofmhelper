"""
The shared page chrome from base.html: the bits that are easy to drop in a
redesign and only show up as a broken phone layout or a lost accessibility
point -- viewport meta, skip link, the labelled nav toggle, and
aria-current on the active nav item.
"""

import os
import re

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ["APP_PASSWORD_VA"] = "test-va"
os.environ.setdefault("SESSION_SECRET", "test-secret")

import pytest
from fastapi.testclient import TestClient

from ofmhelpers.web.main import app


@pytest.fixture
def client():
    c = TestClient(app)
    c.post("/login", data={"password": "test-admin", "next": "/"})
    return c


def test_every_page_ships_the_responsive_accessible_chrome(client):
    html = client.get("/generate").text

    assert '<html lang="en">' in html
    assert 'name="viewport"' in html
    assert 'class="skip-link" href="#main"' in html
    assert 'id="main"' in html
    assert 'aria-label="Toggle navigation"' in html


def test_active_nav_item_is_marked_for_assistive_tech(client):
    html = client.get("/action-log").text

    # Exactly one link is current, and it is the Action log one.
    assert html.count('aria-current="page"') == 1
    assert re.search(r'<a href="/action-log"\s+aria-current="page"', html)


def _nav(html: str) -> tuple[list[str], list[str]]:
    """The header's (bar links, links folded under More)."""
    header = html.split('id="site-nav"')[1].split("</nav>", maxsplit=1)[0]
    bar, more = header.split('class="nav-more"')
    return re.findall(r'<a href="([^"]+)"', bar), re.findall(r'<a href="([^"]+)"', more)


def test_bar_links_then_the_rest_under_more(client):
    bar, more = _nav(client.get("/generate").text)
    assert bar == [
        "/generate",
        "/clone",
        "/reels",
        "/download-assets",
        "/accounts",
        "/phones",
        "/drive",
    ]
    assert more == [
        "/uniqify",
        "/character-sheet",
        "/applications",
        "/file-manager",
        "/action-log",
        "/cookies",
    ]
    # A page under More marks it.
    assert "<summary data-current>More</summary>" in client.get("/action-log").text


def test_va_more_has_no_admin_pages():
    va = TestClient(app)
    va.post("/login", data={"password": "test-va", "next": "/"})
    _bar, more = _nav(va.get("/generate").text)
    assert more == ["/uniqify"]


def test_login_page_is_standalone_but_still_responsive():
    html = TestClient(app).get("/login").text

    assert '<html lang="en">' in html
    assert 'name="viewport"' in html
    assert 'action="/login"' in html


@pytest.mark.parametrize("path", ["/generate", "/login", "/"])
def test_every_page_head_links_the_favicon_and_it_loads_signed_out(client, path):
    """App pages, the login page and the public landing page all carry it,
    and the icon files are public: the tab shows it before you sign in."""
    html = client.get(path).text
    links = re.findall(r'<link rel="(?:icon|apple-touch-icon)" href="([^"]+)"', html)

    assert len(links) == 3
    signed_out = TestClient(app)
    for href in links:
        response = signed_out.get(href)
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("image/")
