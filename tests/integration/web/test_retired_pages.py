"""Retired pages (`schemas/access.py` `RETIRED`; owner 2026-11-13: /reels,
/clone-links and /repurpose, nobody uses them): switched off whole, code and data kept.
Nobody opens one, no menu lists it, and nothing of it runs in the
background."""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ.setdefault("SESSION_SECRET", "test-secret")

import pytest
from fastapi.testclient import TestClient
from signin import sign_in, sign_in_admin

from ofmhelpers.db.stores.jobs import list_jobs
from ofmhelpers.features.cookies.service import UNLOCK_TASK
from ofmhelpers.features.jobs.sweeps import sweeps
from ofmhelpers.schemas.access import RETIRED, Area, areas_of
from ofmhelpers.schemas.nav import NAV, nav
from ofmhelpers.schemas.users import Role
from ofmhelpers.web.main import app

PAGES = ["/reels", "/clone-links", "/repurpose"]


def test_reels_clone_links_and_repurpose_are_the_retired_ones():
    assert frozenset({Area.REELS, Area.CLONE_LINKS, Area.REPURPOSE}) == RETIRED


@pytest.mark.parametrize("page", PAGES)
def test_nobody_opens_a_retired_page(page):
    admin = TestClient(app)
    sign_in_admin(admin)
    user = TestClient(app)
    sign_in(user)

    for client in (admin, user):
        r = client.get(page, follow_redirects=False)
        assert r.status_code == 403
        assert r.json() == {"detail": "Not available to you"}


def test_a_retired_pages_other_routes_are_off_too():
    admin = TestClient(app)
    sign_in_admin(admin)

    assert admin.post("/reels/watch", data={"profiles": "someone"}).status_code == 403
    assert admin.get("/clone-links/gallery").status_code == 403


@pytest.mark.parametrize("role", list(Role))
def test_no_menu_and_no_role_has_a_retired_page(role):
    assert not RETIRED & set(areas_of(role))
    assert not set(PAGES) & {item.href for item in nav(role)}
    # Its menu entry is kept for the day it comes back.
    assert set(PAGES) <= {item.href for item in NAV}


def test_the_home_page_and_the_header_do_not_link_a_retired_page():
    admin = TestClient(app)
    sign_in_admin(admin)

    html = admin.get("/home").text

    for page in PAGES:
        assert f'href="{page}"' not in html


def test_the_reels_rescan_sweep_never_runs():
    watch = next(sweep for sweep in sweeps() if sweep.name == "reel watch")

    assert watch.enabled() is False


def test_a_cookies_upload_starts_no_reels_job():
    client = TestClient(app)
    user = sign_in(client)
    jar = (
        b"# Netscape HTTP Cookie File\n"
        b".instagram.com\tTRUE\t/\tTRUE\t1893456000\tsessionid\tsecret-value\n"
    )

    r = client.post(
        "/cookies",
        files={"file": ("cookies.txt", jar, "text/plain")},
        follow_redirects=False,
    )

    assert r.status_code in (200, 303)
    assert [
        j for j in list_jobs() if j.task == UNLOCK_TASK and j.actor == str(user.id)
    ] == []
