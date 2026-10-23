"""
The shared page chrome from base.html: the bits that are easy to drop in a
redesign and only show up as a broken phone layout or a lost accessibility
point -- viewport meta, skip link, the labelled nav toggle, and
aria-current on the active nav item.
"""

import os
import re

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ.setdefault("SESSION_SECRET", "test-secret")

import pytest
from fastapi.testclient import TestClient
from signin import sign_in

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
        "/clone-long",
        "/download-assets",
        "/accounts",
        "/links",
        "/phones",
        "/drive",
    ]
    # Viral retired from the bar (2026-10-23).
    assert more == [
        "/reels",
        "/uniqify",
        "/account",
        "/cookies",
        "/users",
        "/character-sheet",
        "/applications",
        "/file-manager",
        "/styleguide",
        "/action-log",
    ]
    # A page under More marks it.
    assert "<summary data-current>More</summary>" in client.get("/action-log").text


def test_a_users_more_has_no_admin_pages():
    va = TestClient(app)
    sign_in(va)
    _bar, more = _nav(va.get("/generate").text)
    assert more == ["/reels", "/uniqify", "/account", "/cookies"]


def test_login_page_is_standalone_but_still_responsive():
    html = TestClient(app).get("/login").text

    assert '<html lang="en">' in html
    assert 'name="viewport"' in html
    assert 'action="/login"' in html


@pytest.fixture
def signed_out():
    return TestClient(app)


@pytest.mark.parametrize(
    ("path", "who"),
    [("/generate", "client"), ("/login", "signed_out"), ("/", "signed_out")],
)
def test_every_page_head_links_the_favicon_and_it_loads_signed_out(
    request, signed_out, path, who
):
    """App pages, the login page and the public landing page all carry it,
    and the icon files are public: the tab shows it before you sign in.
    (Signed in, /login and / send you to /home.)"""
    html = request.getfixturevalue(who).get(path).text
    links = re.findall(r'<link rel="(?:icon|apple-touch-icon)" href="([^"]+)"', html)

    assert len(links) == 3
    for href in links:
        response = signed_out.get(href)
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("image/")


def test_the_brand_goes_home_signed_in_and_to_the_landing_page_signed_out(
    client, signed_out
):
    assert '<a class="brand" href="/home">' in client.get("/generate").text
    assert '<a class="brand" href="/">' in signed_out.get("/home").text


def test_the_header_switches_light_and_dark_and_the_choice_sticks(client):
    html = client.get("/generate").text
    assert 'class="icon-btn theme-toggle"' in html
    assert "js/theme.js" in html
    assert 'data-theme="dark"' not in html

    client.cookies.set("ofm_theme", "dark")
    assert '<html lang="en" data-theme="dark">' in client.get("/generate").text
    client.cookies.set("ofm_theme", "light")
    assert 'data-theme="dark"' not in client.get("/generate").text
    client.cookies.set("ofm_theme", '"><script>')
    assert '<script>"' not in client.get("/generate").text
