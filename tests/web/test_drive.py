"""/drive: every Instagram login on an active phone gets "<handle> - <owner>"
in the shared Drive folder and a folder per day inside it; a re-run calls
Drive for nothing, a hand-made folder is reused, a new owner renames, a
deleted folder comes back; the page shows each day folder's files with
previews proxied through the app."""

import datetime
import itertools
import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ["APP_PASSWORD_VA"] = "test-va"
os.environ.setdefault("SESSION_SECRET", "test-secret")

import pytest
from fastapi.testclient import TestClient

from ofmhelpers.cache import get_redis
from ofmhelpers.gdrive import DriveError, DriveFile, DriveFolder, FolderGoneError
from ofmhelpers.gdrive.models import FOLDER_MIME
from ofmhelpers.web import drive_folders
from ofmhelpers.web.main import app
from ofmhelpers.web.schemas import DeviceFields, LoginFields, day_folder_name
from ofmhelpers.web.stores import devices
from ofmhelpers.web.stores import drive_folders as store

DAY = datetime.date(2026, 10, 21)
ROOT = "root-folder-id"


class FakeDrive:
    """Drive as a dict of items; counts every call so a test can tell a
    sweep did nothing."""

    def __init__(self) -> None:
        self.items: dict[str, dict] = {
            ROOT: {"name": "ALL ACCOUNTS", "parent": None, "mime": FOLDER_MIME}
        }
        self.calls: list[str] = []
        self.shared = True
        self.fail_on: set[str] = set()
        self._ids = itertools.count(1)

    def add(self, parent: str, name: str, mime: str = FOLDER_MIME, **extra) -> str:
        item_id = f"fake-item-{next(self._ids):06d}"
        self.items[item_id] = {"name": name, "parent": parent, "mime": mime, **extra}
        return item_id

    def _live(self, item_id: str) -> dict:
        item = self.items.get(item_id)
        if item is None or item.get("trashed") or (item_id == ROOT and not self.shared):
            msg = f"Folder {item_id}: not found"
            raise FolderGoneError(msg)
        return item

    def folder(self, folder_id: str) -> DriveFolder:
        self.calls.append("folder")
        return DriveFolder(id=folder_id, name=self._live(folder_id)["name"])

    def children(
        self, parent_id: str, *, folders_only: bool = False
    ) -> list[DriveFile]:
        self.calls.append("children")
        self._live(parent_id)
        return [
            DriveFile(
                id=i,
                name=x["name"],
                mime_type=x["mime"],
                has_thumbnail=x.get("thumb", False),
            )
            for i, x in self.items.items()
            if x["parent"] == parent_id
            and not x.get("trashed")
            and (not folders_only or x["mime"] == FOLDER_MIME)
        ]

    def find_folder(self, parent_id: str, name: str) -> DriveFolder | None:
        self.calls.append("find")
        for i, x in self.items.items():
            if x["parent"] == parent_id and x["name"] == name and not x.get("trashed"):
                return DriveFolder(id=i, name=name)
        return None

    def create_folder(self, parent_id: str, name: str) -> DriveFolder:
        self.calls.append("create")
        if name in self.fail_on:
            msg = f"Creating {name!r}: Drive refused"
            raise DriveError(msg)
        self._live(parent_id)
        return DriveFolder(id=self.add(parent_id, name), name=name)

    def ensure_folder(self, parent_id: str, name: str) -> DriveFolder:
        return self.find_folder(parent_id, name) or self.create_folder(parent_id, name)

    def rename(self, folder_id: str, name: str) -> DriveFolder:
        self.calls.append("rename")
        self.items[folder_id]["name"] = name
        return DriveFolder(id=folder_id, name=name)

    def thumbnail(self, file_id: str, _px: int) -> tuple[bytes, str] | None:
        self.calls.append("thumbnail")
        if not self.items.get(file_id, {}).get("thumb"):
            return None
        return b"\xff\xd8jpeg", "image/jpeg"

    def named(self, name: str, parent: str = ROOT) -> list[str]:
        return [
            i
            for i, x in self.items.items()
            if x["name"] == name and x["parent"] == parent and not x.get("trashed")
        ]


@pytest.fixture
def drive(monkeypatch) -> FakeDrive:
    fake = FakeDrive()
    monkeypatch.setenv("OFM_GDRIVE_SERVICE_ACCOUNT", "{}")
    monkeypatch.setenv("OFM_GDRIVE_ROOT_FOLDER", ROOT)
    monkeypatch.setattr(drive_folders, "connect", lambda: fake)
    monkeypatch.setattr(drive_folders, "service_account_email", lambda: "bot@x.iam")
    monkeypatch.setattr(drive_folders, "today", lambda: DAY)
    return fake


def _client(password: str = "test-va") -> TestClient:
    c = TestClient(app)
    c.post("/login", data={"password": password, "next": "/"})
    return c


def _phone(label: str, owner: str = "Anna", **extra) -> int:
    return devices.add_device(
        DeviceFields(
            label=label, model_id="iphone-13", color_id="blue", owner=owner, **extra
        )
    )


def _login(device_id: int, service: str, username: str) -> int:
    added = devices.add_login(
        device_id, LoginFields(service=service, username=username)
    )
    assert added is not None
    return added[0]


def _day_folder(drive: FakeDrive, handle: str, owner: str = "Anna") -> str:
    (account,) = drive.named(f"{handle} - {owner}")
    (day,) = drive.named(day_folder_name(DAY), parent=account)
    return day


# --- names ---------------------------------------------------------------------


def test_day_folder_is_month_and_day_without_zero():
    assert day_folder_name(datetime.date(2026, 1, 1)) == "Jan 1"
    assert day_folder_name(DAY) == "Oct 21"


def test_today_follows_the_configured_timezone(monkeypatch):
    monkeypatch.setenv("OFM_GDRIVE_TIMEZONE", "Pacific/Kiritimati")
    east = drive_folders.today()
    monkeypatch.setenv("OFM_GDRIVE_TIMEZONE", "Pacific/Pago_Pago")
    west = drive_folders.today()
    assert east - west == datetime.timedelta(days=1)


# --- sync ----------------------------------------------------------------------


def test_sync_makes_a_folder_and_a_day_folder_per_instagram_login(drive):
    phone = _phone("Phone 01", owner="Anna")
    _login(phone, "instagram", "ann.ig")
    _login(phone, "gmail", "a@gmail.com")
    retired = _phone("Phone 02", owner="Bob")
    _login(retired, "instagram", "bob.ig")
    devices.retire_device(retired)

    report = drive_folders.sync()

    assert report.created == ["ann.ig - Anna"]
    assert report.days == 1
    assert not report.failed
    assert _day_folder(drive, "ann.ig")
    assert not drive.named("bob.ig - Bob")


def test_second_sync_the_same_day_never_calls_drive(drive):
    _login(_phone("Phone 01"), "instagram", "ann.ig")
    drive_folders.sync()
    drive.calls.clear()

    report = drive_folders.sync()

    assert drive.calls == []
    assert report.created == []
    assert report.days == 0


def test_next_day_adds_only_the_day_folder(drive, monkeypatch):
    _login(_phone("Phone 01"), "instagram", "ann.ig")
    drive_folders.sync()
    tomorrow = DAY + datetime.timedelta(days=1)

    report = drive_folders.sync(tomorrow)

    assert report.created == []
    assert report.days == 1
    (account,) = drive.named("ann.ig - Anna")
    assert drive.named(day_folder_name(tomorrow), parent=account)


def test_hand_made_folders_are_reused_not_duplicated(drive):
    _login(_phone("Phone 01"), "instagram", "ann.ig")
    account = drive.add(ROOT, "ann.ig - Anna")
    day = drive.add(account, "Oct 21")

    report = drive_folders.sync()

    assert report.adopted == ["ann.ig - Anna"]
    assert report.created == []
    assert drive.named("ann.ig - Anna") == [account]
    assert store.day_folders(DAY) == {store.targets()[0].login_id: day}


def test_new_owner_renames_the_folder(drive):
    phone = _phone("Phone 01", owner="Anna")
    _login(phone, "instagram", "ann.ig")
    drive_folders.sync()
    (account,) = drive.named("ann.ig - Anna")
    devices.update_device(
        phone,
        DeviceFields(
            label="Phone 01", model_id="iphone-13", color_id="blue", owner="Kate"
        ),
    )

    report = drive_folders.sync()

    assert report.renamed == ["ann.ig - Kate"]
    assert drive.named("ann.ig - Kate") == [account]
    assert report.days == 0


def test_deleted_folder_is_made_again(drive):
    _login(_phone("Phone 01"), "instagram", "ann.ig")
    drive_folders.sync()
    (account,) = drive.named("ann.ig - Anna")
    drive.items[account]["trashed"] = True
    store.forget_day_folder(store.targets()[0].login_id, DAY)

    report = drive_folders.sync()

    assert report.created == ["ann.ig - Anna"]
    assert report.days == 1
    assert _day_folder(drive, "ann.ig")


def test_one_failing_login_does_not_stop_the_others(drive):
    _login(_phone("Phone 01"), "instagram", "ann.ig")
    _login(_phone("Phone 02", owner="Bob"), "instagram", "bob.ig")
    drive.fail_on.add("ann.ig - Anna")

    report = drive_folders.sync()

    assert len(report.failed) == 1
    assert "ann.ig - Anna" in report.failed[0]
    assert _day_folder(drive, "bob.ig", owner="Bob")
    # Retried on the next sweep.
    drive.fail_on.clear()
    assert drive_folders.sync().created == ["ann.ig - Anna"]


def test_a_login_on_two_phones_gets_one_folder(drive):
    first = _phone("Phone 01", owner="Anna")
    second = _phone("Phone 02", owner="Bob")
    _login(first, "instagram", "ann.ig")
    _login(second, "instagram", "ann.ig")

    assert drive_folders.sync().created == ["ann.ig - Anna"]


def test_root_not_shared_raises_and_changes_nothing(drive):
    _login(_phone("Phone 01"), "instagram", "ann.ig")
    drive.shared = False
    with pytest.raises(FolderGoneError):
        drive_folders.sync()
    assert store.account_folders() == {}


def test_concurrent_sync_is_skipped(drive):
    _login(_phone("Phone 01"), "instagram", "ann.ig")
    get_redis().set("lock:drive:sync", "1")
    try:
        assert drive_folders.sync().busy
        assert drive.calls == []
    finally:
        get_redis().delete("lock:drive:sync")


def test_deleting_the_login_forgets_its_folders(drive):
    phone = _phone("Phone 01")
    login = _login(phone, "instagram", "ann.ig")
    drive_folders.sync()
    devices.remove_login(phone, login)
    assert store.account_folders() == {}
    assert store.day_folders(DAY) == {}
    # Drive keeps the folder and what is in it.
    assert drive.named("ann.ig - Anna")


# --- page ----------------------------------------------------------------------


def test_page_shows_today_files_with_previews(drive):
    _login(_phone("Phone 01"), "instagram", "ann.ig")
    _login(_phone("Phone 02", owner="Bob"), "instagram", "bob.ig")
    drive_folders.sync()
    day = _day_folder(drive, "ann.ig")
    video = drive.add(day, "reel1.mp4", mime="video/mp4", thumb=True)
    drive.add(day, "notes.txt", mime="text/plain")
    drive.add(ROOT, "old messy folder")

    html = _client().get("/drive").text

    assert "Drive · Oct 21" in html
    assert "1 of 2 accounts uploaded today · 2 files" in html
    assert f'src="/drive/thumb/{video}"' in html
    assert "notes.txt" in html
    assert "Nothing uploaded today" in html
    assert "old messy folder" in html
    assert "ann.ig - Anna" not in html.split("other folder")[-1]


def test_page_without_setup_says_what_is_missing(monkeypatch):
    monkeypatch.delenv("OFM_GDRIVE_SERVICE_ACCOUNT", raising=False)
    html = _client().get("/drive").text
    assert "Google Drive is not set up" in html
    assert 'id="dr-sync" disabled' in html


def test_page_not_shared_names_the_bot(drive):
    _login(_phone("Phone 01"), "instagram", "ann.ig")
    drive.shared = False
    html = _client().get("/drive").text
    assert "share it with bot@x.iam as Editor" in html


def test_page_forgets_a_deleted_day_folder(drive):
    _login(_phone("Phone 01"), "instagram", "ann.ig")
    drive_folders.sync()
    drive.items[_day_folder(drive, "ann.ig")]["trashed"] = True

    html = _client().get("/drive").text

    assert "Today&#39;s folder was deleted" in html
    assert store.day_folders(DAY) == {}
    assert drive_folders.sync().days == 1


def test_drive_is_in_the_nav():
    assert 'href="/drive"' in _client().get("/phones").text


def test_page_needs_login():
    response = TestClient(app).get("/drive", follow_redirects=False)
    assert response.status_code == 303


# --- routes --------------------------------------------------------------------


def test_sync_route_reports(drive):
    _login(_phone("Phone 01"), "instagram", "ann.ig")
    response = _client().post("/drive/sync")
    assert response.status_code == 200
    assert response.json()["created"] == ["ann.ig - Anna"]


def test_sync_route_not_shared_is_502(drive):
    _login(_phone("Phone 01"), "instagram", "ann.ig")
    drive.shared = False
    response = _client().post("/drive/sync")
    assert response.status_code == 502
    assert "not found" in response.json()["detail"]


def test_thumb_route_serves_and_caches_privately(drive):
    file_id = drive.add(ROOT, "a.jpg", mime="image/jpeg", thumb=True)
    response = _client().get(f"/drive/thumb/{file_id}")
    assert response.status_code == 200
    assert response.content == b"\xff\xd8jpeg"
    assert response.headers["cache-control"] == "private, max-age=86400"


def test_thumb_route_without_preview_is_404(drive):
    file_id = drive.add(ROOT, "a.txt", mime="text/plain")
    assert _client().get(f"/drive/thumb/{file_id}").status_code == 404


def test_thumb_route_rejects_a_bad_id(drive):
    assert _client().get("/drive/thumb/..%2Fetc").status_code in (404, 422)
    assert drive.calls == []
