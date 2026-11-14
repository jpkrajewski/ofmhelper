"""The header, its More menu and the home cards render from one list,
schemas.nav.NAV: a page is one entry, shown only to who may open it."""

import os
import re

os.environ.setdefault("SESSION_SECRET", "test-secret")

import pytest
from fastapi.testclient import TestClient
from repo_paths import SRC
from signin import sign_in, sign_in_admin

from ofmhelpers.auth.access import RETIRED
from ofmhelpers.schemas.nav import NAV, NavPlace, nav
from ofmhelpers.schemas.users import Role
from ofmhelpers.web.main import app

ICONS = (SRC / "web" / "templates" / "_icons.html").read_text("utf-8")


def _header(html: str) -> str:
    return html[html.index('<nav class="site-nav"') : html.index("</nav>")]


def test_every_entry_has_an_icon_and_opens_a_page():
    client = TestClient(app)
    sign_in_admin(client)
    for item in NAV:
        assert f'"{item.icon}":' in ICONS, item.icon
        # A retired page's entry stays for the day it is back; it opens for nobody.
        opens = 403 if item.area in RETIRED else 200
        assert client.get(item.href).status_code == opens, item.href


def test_a_user_never_gets_an_admin_page():
    admin_pages = {
        "/chatting",
        "/users",
        "/character-sheet",
        "/applications",
        "/file-manager",
        "/styleguide",
        "/action-log",
    }
    live = [item for item in NAV if item.area not in RETIRED]
    assert nav(Role.ADMIN) == live
    assert [item.href for item in nav(Role.USER)] == [
        item.href for item in live if item.href not in admin_pages
    ]
    assert len(nav(Role.USER)) == len(live) - len(admin_pages)


def test_a_va_gets_the_four_pages_they_work_in():
    assert [item.label for item in nav(Role.VA)] == [
        "Gen AI",
        "Clone",
        "Download",
        "Edit",
    ]


def test_header_and_home_render_only_a_vas_pages():
    client = TestClient(app)
    sign_in(client, "nav-va@example.com", Role.VA)
    html = client.get("/home").text
    assert re.findall(r'<a href="(/[^"]*)"', _header(html)) == [
        "/generate",
        "/clone",
        "/download-assets",
        "/edit",
    ]
    assert html.count('class="link-card"') == 4


@pytest.mark.parametrize(
    ("path", "current"),
    [
        ("/phones", True),
        ("/phones/export.xlsx", True),
        ("/phones-old", False),
        ("/", False),
    ],
)
def test_current_page_is_the_page_or_one_under_it(path, current):
    phones = next(item for item in NAV if item.href == "/phones")
    assert phones.is_current(path) is current


def test_header_and_home_render_the_list_for_a_user():
    client = TestClient(app)
    sign_in(client)
    html = client.get("/home").text
    header = _header(html)
    shown = nav(Role.USER)
    assert re.findall(r'<a href="(/[^"]*)"', header) == [
        i.href for i in shown if i.place == NavPlace.BAR
    ] + [i.href for i in shown if i.place == NavPlace.MORE]
    assert "/users" not in header
    # Home: one card per page; the footer repeats nothing.
    assert html.count('class="link-card"') == len(shown)
    footer = html[html.index('<footer class="site-footer">') :]
    assert "<a " not in footer


def test_admin_sees_admin_pages_and_the_current_one_is_marked():
    client = TestClient(app)
    sign_in_admin(client)
    header = _header(client.get("/users").text)
    assert '<a href="/users" aria-current="page">Users</a>' in header
    assert "<summary data-current>More</summary>" in header
