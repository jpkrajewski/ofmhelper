"""
A page's guided tour (ui.tour, static/js/tour.js; owner 2026-11-14): /generate,
/clone and /download-assets light up the VA's job one control at a time. The steps
are on the page for everyone behind "How to"; a VA's are marked to start by
themselves, once per sign-in (the browser part: tests/e2e/test_tour_page.py).
Every step points at a control that is really on the page.
"""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ.setdefault("SESSION_SECRET", "test-secret")

import re

import pytest
from fastapi.testclient import TestClient
from signin import make_user, sign_in, sign_in_admin

from ofmhelpers.db.stores import users
from ofmhelpers.schemas import Role
from ofmhelpers.web.main import app

OWNER = "owner@example.com"
VA = "va@example.com"
PAGES = [("/generate", 4), ("/clone", 4), ("/download-assets", 3)]


def _va_client() -> TestClient:
    owner = make_user(OWNER)
    client = TestClient(app)
    va = sign_in(client, VA, Role.VA)
    users.set_owner(va.id, owner.id)
    return client


def _tip(html: str) -> str:
    found = re.search(r'<div class="tour-tip".*?</ol>', html, re.DOTALL)
    assert found, "no tour on the page"
    return found.group(0)


def _steps(html: str) -> list[tuple[str, str]]:
    return re.findall(r'<li data-target="([^"]+)">([^<]+)</li>', _tip(html))


@pytest.mark.parametrize(("page", "count"), PAGES)
def test_a_vas_page_starts_its_tour_by_itself_once_per_sign_in(page, count):
    client = _va_client()

    html = client.get(page).text
    tip = _tip(html)

    assert " data-auto hidden>" in tip
    # When this session signed in: what the browser compares against.
    (login,) = re.findall(r'data-login="(\d+)"', tip)
    assert int(login) > 0
    assert len(_steps(html)) == count
    assert "tour-open" in html
    assert "js/tour.js" in html
    # The same sign-in, the same mark: a reload does not start it again.
    assert f'data-login="{login}"' in client.get(page).text


def test_the_clone_tour_is_the_owners_four_steps_in_order():
    steps = _steps(_va_client().get("/clone").text)

    assert [text for _target, text in steps] == [
        "Pick the picture of our model.",
        "Add the video you want to clone.",
        "Text on the video? Press Remove text overlay. Tattoos? Press No tattoos.",
        "Press Start. Then do the next video while this one is made.",
    ]


def test_the_download_tour_ends_on_the_drive_sheet_button():
    steps = _steps(_va_client().get("/download-assets").text)

    assert [text for _target, text in steps] == [
        "Paste the link here and press Run.",
        "When the video is downloaded, press Clone on it.",
        "Every hour press this. Then clone every new video.",
    ]


@pytest.mark.parametrize(
    ("page", "markers"),
    [
        (
            "/clone",
            [
                'class="field file-picker" data-field="image"',
                'class="field file-picker" data-field="videos"',
                'class="chip-row"',
                'id="clone-form"',
            ],
        ),
        (
            "/download-assets",
            ['id="dl-urls"', 'id="results-panel"', "sheet-download"],
        ),
    ],
)
def test_every_step_points_at_a_control_that_is_on_the_page(page, markers):
    html = _va_client().get(page).text

    for marker in markers:
        assert marker in html, marker


@pytest.mark.parametrize("page", [page for page, _ in PAGES])
def test_an_owner_has_the_button_and_nothing_starts_by_itself(page):
    client = TestClient(app)
    sign_in(client, OWNER)

    html = client.get(page).text

    assert "data-auto" not in _tip(html)
    assert "tour-open" in html


def test_an_admin_looking_as_a_va_is_not_toured():
    owner = TestClient(app)
    me = sign_in_admin(owner)
    va = make_user(VA, Role.VA)
    users.set_owner(va.id, me.id)
    owner.post(f"/users/{va.id}/viewing", follow_redirects=False)

    assert "data-auto" not in _tip(owner.get("/clone").text)


def test_a_page_without_a_tour_has_none():
    client = TestClient(app)
    sign_in(client, OWNER)

    html = client.get("/clone-long").text

    assert "tour-tip" not in html
    assert "tour-open" not in html
