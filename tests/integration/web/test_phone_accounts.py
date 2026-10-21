"""A phone's Instagram login is an /accounts account: adding (or renaming)
it tracks the handle and scans it, the account lists the phone, and
/phones shows each login's followers and sorts phones by them -- for any
service whose services.json entry names a tracked platform. Instagram here
is the fake (OFM_FAKE_EXTERNALS), so the scan is real code, offline."""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ["APP_PASSWORD_VA"] = "test-va"
os.environ.setdefault("SESSION_SECRET", "test-secret")

import pytest
from fastapi.testclient import TestClient

from ofmhelpers.web.main import app
from ofmhelpers.web.schemas import DeviceFields
from ofmhelpers.web.schemas.devices import _compact
from ofmhelpers.web.stores import devices as store
from ofmhelpers.web.stores.tracked_accounts import list_accounts, untrack


@pytest.fixture
def admin(fake_externals) -> TestClient:
    c = TestClient(app)
    c.post("/login", data={"password": "test-admin", "next": "/"})
    return c


def _phone(label: str) -> int:
    return store.add_device(
        DeviceFields(label=label, model_id="iphone-13", color_id="blue", owner="Anna")
    )


def _add(admin: TestClient, phone: int, service: str, username: str) -> int:
    r = admin.post(
        f"/phones/{phone}/logins", json={"service": service, "username": username}
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _tracked() -> dict[str, int | None]:
    """handle -> followers of every tracked account."""
    return {a.handle: a.followers for a in list_accounts()}


def test_an_instagram_login_is_tracked_scanned_and_shows_its_phone(admin):
    phone = _phone("Phone 07")
    _add(admin, phone, "instagram", "@Yuki_Kanasiva")
    _add(admin, phone, "gmail", "yuki@gmail.com")

    # The handle as Instagram writes it; scanned at once (worker inline here).
    assert _tracked() == {"yuki_kanasiva": 850}
    html = admin.get("/accounts").text
    assert "@yuki_kanasiva" in html
    assert "Phone 07" in html


def test_renaming_the_login_tracks_the_new_handle(admin):
    phone = _phone("Phone 07")
    login = _add(admin, phone, "instagram", "yuki_kanasiva")

    r = admin.post(
        f"/phones/logins/{login}",
        json={"service": "instagram", "username": "sheep_love33"},
    )

    assert r.status_code == 200
    assert _tracked() == {"yuki_kanasiva": 850, "sheep_love33": 12000}


def test_an_account_removed_by_hand_comes_back_only_when_added_again(admin):
    phone = _phone("Phone 07")
    _add(admin, phone, "instagram", "yuki_kanasiva")
    untrack([a.id for a in list_accounts()])
    assert _tracked() == {}

    _add(admin, _phone("Phone 08"), "instagram", "yuki_kanasiva")

    assert _tracked() == {"yuki_kanasiva": 850}


def test_a_login_of_an_untracked_service_tracks_nothing(admin):
    _add(admin, _phone("Phone 07"), "x", "brand")
    assert _tracked() == {}


def test_phones_show_followers_and_sort_by_them_by_default(admin):
    small = _phone("A small")
    big = _phone("B big")
    _phone("C none")
    _add(admin, small, "instagram", "sheep_love33")
    _add(admin, big, "instagram", "brookslockvip")

    html = admin.get("/phones").text

    assert '<span class="ph-login-followers">12k followers</span>' in html
    assert '<span class="ph-login-followers">200k followers</span>' in html
    order = [html.index(label) for label in ("B big", "A small", "C none")]
    assert order == sorted(order)
    assert '<option value="followers" selected>Sort: Most followers</option>' in html


@pytest.mark.parametrize(
    ("n", "shown"),
    [(0, "0"), (950, "950"), (1000, "1k"), (12_340, "12.3k"), (1_250_000, "1.2M")],
)
def test_follower_counts_are_compact(n, shown):
    assert _compact(n) == shown
