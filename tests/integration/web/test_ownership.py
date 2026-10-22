"""Each person sees and changes only their own phones, logins, tracked
accounts, watched profiles, jobs and Drive cards -- admins included: an
admin manages people, never reads their work. Only the background sweeps
see everyone's. What was unique for the whole agency is unique per owner."""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ.setdefault("SESSION_SECRET", "test-secret")

import pytest
from fastapi.testclient import TestClient
from signin import make_user, sign_in, sign_in_admin, sign_in_agency

from ofmhelpers.gdrive.fake import FakeDrive, fake_drive
from ofmhelpers.web import drive_folders, reel_watch
from ofmhelpers.web.main import app
from ofmhelpers.web.schemas import DeviceFields
from ofmhelpers.web.stores import devices, users
from ofmhelpers.web.stores.tracked_accounts import list_accounts
from ofmhelpers.web.stores.watched_profiles import list_watched

PHONE = {
    "label": "Phone 01",
    "model_id": "iphone-13",
    "color_id": "blue",
    "owner": "Anna",
}


def _client(email: str) -> TestClient:
    c = TestClient(app)
    sign_in(c, email)
    return c


@pytest.fixture
def ann() -> TestClient:
    return _client("ann@example.com")


@pytest.fixture
def bob() -> TestClient:
    return _client("bob@example.com")


@pytest.fixture(autouse=True)
def _no_scans(monkeypatch):
    """Adding accounts or profiles queues scans: none reach Instagram."""
    monkeypatch.setattr(reel_watch, "queue_due_scans", list)
    from ofmhelpers.web import account_tracker
    from ofmhelpers.web.routers.tracking import accounts

    monkeypatch.setattr(account_tracker, "queue_due_scans", list)
    monkeypatch.setattr(accounts, "queue_due_scans", list)
    from ofmhelpers.web.routers.downloads import reels

    monkeypatch.setattr(reels, "queue_due_scans", list)


def _admin() -> TestClient:
    c = TestClient(app)
    sign_in_admin(c)
    return c


def _agency() -> TestClient:
    """The password account: the agency's admin."""
    c = TestClient(app)
    sign_in_agency(c)
    return c


def _add_phone(client: TestClient, **extra) -> int:
    r = client.post("/phones/add", json=PHONE | extra)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _add_login(client: TestClient, device: int, username: str, password: str) -> int:
    r = client.post(
        f"/phones/{device}/logins",
        json={"service": "instagram", "username": username, "password": password},
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


# --- phones and logins -----------------------------------------------------------


def test_a_users_phones_are_theirs_alone(ann, bob):
    mine = _add_phone(ann)
    login = _add_login(ann, mine, "ann.ig", "ann-secret")

    assert "ann.ig" in ann.get("/phones").text
    assert "ann.ig" not in bob.get("/phones").text
    assert bob.get(f"/phones/logins/{login}/secret").status_code == 404
    assert bob.post(f"/phones/{mine}/delete").status_code == 404
    assert bob.post(f"/phones/{mine}", json=PHONE | {"label": "x"}).status_code == 404
    assert "ann.ig" not in bob.get("/phones/export.xlsx").content.decode("latin-1")


def test_an_admin_does_not_see_or_change_a_users_phone(ann):
    mine = _add_phone(ann, label="Ann private phone")
    login = _add_login(ann, mine, "ann.ig", "ann-secret")
    for admin in (_admin(), _agency()):
        assert "Ann private phone" not in admin.get("/phones").text
        assert "ann.ig" not in admin.get("/phones").text
        assert admin.get(f"/phones/logins/{login}/secret").status_code == 404
        export = admin.get("/phones/export.xlsx").content.decode("latin-1")
        assert "ann.ig" not in export
        assert admin.post(f"/phones/{mine}/retire").status_code == 404
        assert admin.post(f"/phones/{mine}/delete").status_code == 404
        assert admin.post(f"/phones/{mine}", json=PHONE).status_code == 404
    assert "ann.ig" in ann.get("/phones").text


def test_two_users_may_use_the_same_label_and_the_same_login(ann, bob):
    """Unique per owner: adding the same account never links to (or
    reveals) the other user's login with its password."""
    a = _add_phone(ann)
    b = _add_phone(bob)
    a_login = _add_login(ann, a, "shared.ig", "ann-pw")
    r = bob.post(
        f"/phones/{b}/logins",
        json={"service": "instagram", "username": "shared.ig", "password": "bob-pw"},
    )
    assert r.json()["linked"] is False
    b_login = r.json()["id"]
    assert b_login != a_login
    assert bob.get(f"/phones/logins/{b_login}/secret").json()["password"] == "bob-pw"
    assert ann.get(f"/phones/logins/{a_login}/secret").json()["password"] == "ann-pw"


def test_a_phone_login_is_tracked_for_the_phones_owner(ann, bob):
    device = _add_phone(ann)
    _add_login(ann, device, "ann.ig", "")
    ann_id = users.by_email("ann@example.com").id
    assert [(a.handle, a.user_id) for a in list_accounts()] == [("ann.ig", ann_id)]
    assert "ann.ig" not in bob.get("/accounts").text


# --- tracked accounts ------------------------------------------------------------


def test_tracked_accounts_and_creators_are_per_user(ann, bob):
    ann.post(
        "/accounts/add",
        json={"platform": "instagram", "handles": "yuki", "creator": "Mia"},
    )
    r = bob.post("/accounts/add", json={"platform": "instagram", "handles": "yuki"})
    assert r.json() == {"added": ["yuki"], "existing": 0}  # Bob's own row

    ann_account = next(a for a in list_accounts(users.by_email("ann@example.com").id))
    assert ann_account.creator == "Mia"
    assert bob.get(f"/accounts/{ann_account.id}").status_code == 404
    assert bob.post("/accounts/delete", json={"ids": [ann_account.id]}).json() == {
        "count": 0
    }
    assert bob.post(
        "/accounts/creator", json={"ids": [ann_account.id], "creator": "X"}
    ).json() == {"count": 0}
    assert "Mia" not in bob.get("/accounts").text


def test_an_admin_does_not_see_or_change_a_users_account(ann):
    ann.post(
        "/accounts/add",
        json={"platform": "instagram", "handles": "ann.only", "creator": "Mia"},
    )
    ann_id = users.by_email("ann@example.com").id
    account = next(a for a in list_accounts(ann_id) if a.handle == "ann.only")
    for admin in (_admin(), _agency()):
        assert "ann.only" not in admin.get("/accounts").text
        assert admin.get(f"/accounts/{account.id}").status_code == 404
        assert (
            admin.post(f"/accounts/{account.id}/notes", json={"notes": "x"}).status_code
            == 404
        )
        assert admin.post("/accounts/delete", json={"ids": [account.id]}).json() == {
            "count": 0
        }
    assert "ann.only" in ann.get("/accounts").text


# --- reels watch list ------------------------------------------------------------


def test_watch_lists_and_groups_are_per_user_one_scan_each(ann, bob, monkeypatch):
    ann.post("/reels/watch", data={"profiles": "star", "group": "asian"})
    bob.post("/reels/watch", data={"profiles": "star", "group": "gay"})

    ann_id = users.by_email("ann@example.com").id
    bob_id = users.by_email("bob@example.com").id
    assert [(p.username, p.group) for p in list_watched(owner=ann_id)] == [
        ("star", "asian")
    ]
    assert [(p.username, p.group) for p in list_watched(owner=bob_id)] == [
        ("star", "gay")
    ]
    assert [p.username for p in list_watched()] == ["star"]  # the sweeper: once

    claimed = reel_watch.claim_due(0, 3600)
    assert claimed == ["star"]  # scanned once for both

    bob.post("/reels/unwatch/star")
    assert list_watched(owner=bob_id) == []
    assert [p.username for p in list_watched(owner=ann_id)] == ["star"]
    assert 'data-username="star"' not in bob.get("/reels/watchlist").text


def test_an_admin_moves_and_removes_only_their_own_watched_row(ann, bob):
    """An admin's watch list is their own: another user's profile is not on
    it, and a row's dropdown and Remove act on the admin's own row only."""
    admin = TestClient(app)
    admin_id = sign_in_admin(admin).id
    ann.post("/reels/watch", data={"profiles": "shared.star", "group": "asian"})
    bob.post("/reels/watch", data={"profiles": "shared.star", "group": "gay"})
    assert 'data-username="shared.star"' not in admin.get("/reels/watchlist").text
    assert "shared.star" not in admin.get("/reels").text

    assert admin.post("/reels/unwatch/shared.star").status_code == 404
    assert admin.post("/reels/group/shared.star", data={"group": "x"}).status_code == (
        404
    )

    admin.post("/reels/watch", data={"profiles": "shared.star"})
    assert admin.post("/reels/group/shared.star", data={"group": "vip"}).is_success
    ann_id = users.by_email("ann@example.com").id
    bob_id = users.by_email("bob@example.com").id
    assert [(p.username, p.group) for p in list_watched(owner=admin_id)] == [
        ("shared.star", "vip")
    ]
    assert [p.group for p in list_watched(owner=ann_id)] == ["asian"]
    assert [p.group for p in list_watched(owner=bob_id)] == ["gay"]

    assert admin.post("/reels/unwatch/shared.star").is_success
    assert list_watched(owner=admin_id) == []
    assert [p.group for p in list_watched(owner=ann_id)] == ["asian"]
    assert [p.group for p in list_watched(owner=bob_id)] == ["gay"]


def test_an_admins_group_feed_keeps_a_profile_an_older_row_has_elsewhere(ann, bob):
    """Everyone's list (the sweeper's) lists a username once, but only after
    the group filter: bob's "gay" row of a profile ann added first
    (ungrouped) is in its "gay" list."""
    ann.post("/reels/watch", data={"profiles": "grouped.later"})
    bob.post("/reels/watch", data={"profiles": "grouped.later", "group": "gay"})

    assert [(p.username, p.group) for p in list_watched("gay")] == [
        ("grouped.later", "gay")
    ]
    assert [p.group for p in list_watched() if p.username == "grouped.later"] == [None]


# --- Drive -----------------------------------------------------------------------


@pytest.fixture
def drive(fake_externals, monkeypatch, tmp_path) -> FakeDrive:
    """The fake Drive; Ann and Bob each signed in with Google (their own
    Drive connected)."""
    monkeypatch.setenv("OFM_UPLOADS_ROOT", str(tmp_path))
    for email in ("ann@example.com", "bob@example.com"):
        user = make_user(email)
        users._repository().signed_in(user.id, "", f"fake-refresh:{email}")
    return fake_drive()


def test_each_users_folders_live_in_their_own_drive(ann, bob, drive):
    ann_login = _add_login(ann, _add_phone(ann), "ann.ig", "")
    _add_login(bob, _add_phone(bob, owner="Bob"), "bob.ig", "")

    drive_folders.sync()

    ann_root = users.by_email("ann@example.com").drive_root_id
    bob_root = users.by_email("bob@example.com").drive_root_id
    assert ann_root != bob_root
    assert drive.named("DEV - ALL ACCOUNTS", "mydrive-ann@example.com") == [ann_root]
    (ann_folder,) = drive.named("ann.ig - Anna", ann_root)
    assert drive.item(ann_folder).owner == "ann@example.com"
    assert drive.named("bob.ig - Bob", ann_root) == []

    html = ann.get("/drive/live").text
    assert "ann.ig" in html
    assert "bob.ig" not in html
    file_id = drive.add_file(ann_folder, "a.jpg", thumbnail=True)
    assert ann.get(f"/drive/thumb/{ann_login}/{file_id}").status_code == 200
    assert bob.get(f"/drive/thumb/{ann_login}/{file_id}").status_code == 404


def test_an_admin_sees_and_syncs_only_their_own_drive_cards(ann, bob, drive):
    ann_login = _add_login(ann, _add_phone(ann), "ann.ig", "")
    _add_login(bob, _add_phone(bob, owner="Bob"), "bob.ig", "")
    admin = TestClient(app)
    admin_user = sign_in_admin(admin)
    users._repository().signed_in(admin_user.id, "", f"fake-refresh:{admin_user.email}")
    _add_login(admin, _add_phone(admin, owner="Boss"), "boss.ig", "")

    report = admin.post("/drive/sync").json()
    assert "ann.ig" not in str(report)
    assert users.by_email("ann@example.com").drive_root_id is None  # not swept
    for page in ("/drive", "/drive/live"):
        html = admin.get(page).text
        assert "boss.ig" in html
        assert "ann.ig" not in html
        assert "bob.ig" not in html

    drive_folders.sync()  # the background sweep: everyone's
    (ann_folder,) = drive.named(
        "ann.ig - Anna", users.by_email("ann@example.com").drive_root_id
    )
    file_id = drive.add_file(ann_folder, "a.jpg", thumbnail=True)
    assert admin.get(f"/drive/thumb/{ann_login}/{file_id}").status_code == 404
    assert ann.get(f"/drive/thumb/{ann_login}/{file_id}").status_code == 200


# --- removing a user ---------------------------------------------------------------


def test_a_user_who_owns_data_is_deactivated_not_deleted(ann):
    _add_phone(ann)
    ann_id = users.by_email("ann@example.com").id
    admin = TestClient(app)
    sign_in_admin(admin)

    r = admin.post(f"/users/{ann_id}/delete")
    assert r.status_code == 409
    assert "deactivate them instead" in r.text
    assert users.get_user(ann_id) is not None

    empty = make_user("empty@example.com")
    assert (
        admin.post(f"/users/{empty.id}/delete", follow_redirects=False).status_code
        == 303
    )
    assert users.get_user(empty.id) is None


def test_existing_data_belongs_to_the_password_admin():
    """What a store makes without an owner (scripts, the migration's
    backfill) is the agency's: the password admin's."""
    device = devices.add_device(DeviceFields(label="Old phone"))
    found = next(d for d in devices.list_devices() if d.id == device)
    assert found.user_id == users.agency_id()
