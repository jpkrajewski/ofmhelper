import pytest
from fastapi.testclient import TestClient
from signin import sign_in, sign_in_admin

from ofmhelpers.auth.access import RETIRED, Area, areas_of
from ofmhelpers.db.stores.jobs import list_jobs
from ofmhelpers.features.cookies.service import UNLOCK_TASK
from ofmhelpers.features.jobs.sweeps import sweeps
from ofmhelpers.schemas.nav import NAV, nav
from ofmhelpers.schemas.users import Role
from ofmhelpers.web.main import app

PAGES = ["/reels", "/clone-links", "/pictures"]


def test_reels_clone_links_and_pictures_are_the_retired_ones():
    assert frozenset({Area.REELS, Area.CLONE_LINKS, Area.PICTURES}) == RETIRED


@pytest.mark.parametrize("page", PAGES)
def test_nobody_opens_a_retired_page(page):
    admin = TestClient(app)
    sign_in_admin(admin)
    user = TestClient(app)
    sign_in(user)

    for client in (admin, user):
        r = client.get(page, follow_redirects=False)
        assert r.status_code == 404


def test_a_retired_pages_other_routes_are_off_too():
    admin = TestClient(app)
    sign_in_admin(admin)

    assert admin.post("/reels/watch", data={"profiles": "someone"}).status_code == 404
    assert admin.get("/clone-links/gallery").status_code == 404
    assert admin.post("/pictures/run", data={"preset": "1"}).status_code == 404


@pytest.mark.parametrize("role", list(Role))
def test_no_menu_and_no_role_has_a_retired_page(role):
    assert not RETIRED & set(areas_of(role))
    assert not set(PAGES) & {item.href for item in nav(role)}
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
