import re

import pytest
from fastapi.testclient import TestClient
from signin import sign_in, sign_in_agency

from ofmhelpers.web.main import app


@pytest.fixture
def client():
    c = TestClient(app)
    sign_in_agency(c)
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

    assert html.count('aria-current="page"') == 1
    assert re.search(r'<a href="/action-log"\s+aria-current="page"', html)


def _nav(html: str) -> tuple[list[str], list[str]]:
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
        "/chatting",
    ]
    assert more == [
        "/viral",
        "/uniqify",
        "/edit",
        "/handmade",
        "/storage",
        "/trials",
        "/threads",
        "/distribution",
        "/self-links",
        "/spoof",
        "/account",
        "/cookies",
        "/vas",
        "/users",
        "/character-sheet",
        "/applications",
        "/file-manager",
        "/styleguide",
        "/action-log",
    ]
    assert "<summary data-current>More</summary>" in client.get("/action-log").text


def test_a_users_more_has_no_admin_pages():
    va = TestClient(app)
    sign_in(va)
    _bar, more = _nav(va.get("/generate").text)
    assert more == [
        "/viral",
        "/uniqify",
        "/edit",
        "/handmade",
        "/storage",
        "/trials",
        "/threads",
        "/distribution",
        "/self-links",
        "/spoof",
        "/account",
        "/cookies",
        "/vas",
    ]


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
    html = request.getfixturevalue(who).get(path).text
    links = re.findall(r'<link rel="(?:icon|apple-touch-icon)" href="([^"]+)"', html)

    assert len(links) == 3
    for href in links:
        response = signed_out.get(href)
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("image/")


def test_the_brand_goes_home_signed_in(client):
    assert '<a class="brand" href="/home">' in client.get("/generate").text


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
