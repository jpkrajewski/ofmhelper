"""/drive: every Instagram login on an active phone gets "<handle> - <owner>"
in the shared Drive folder and a folder per day ("2026-10-21") inside it; a
re-run writes nothing, a hand-made folder is reused, a new owner renames, a
deleted folder comes back, concurrent sweeps never duplicate, the live
folder is refused outside production; the page shows each day folder's
files with previews proxied through the app, and only those."""

import asyncio
import datetime
import os
import threading
from concurrent.futures import ThreadPoolExecutor

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ["APP_PASSWORD_VA"] = "test-va"
os.environ.setdefault("SESSION_SECRET", "test-secret")

import pytest
import time_machine
from fastapi.testclient import TestClient

from ofmhelpers.gdrive import FolderGoneError, connect
from ofmhelpers.gdrive.fake import FAKE_BOT_EMAIL, FakeDrive
from ofmhelpers.web import drive_folders
from ofmhelpers.web.drive_folders import ProductionFolderError
from ofmhelpers.web.main import app
from ofmhelpers.web.schemas import DeviceFields, LoginFields, day_folder_name
from ofmhelpers.web.stores import devices
from ofmhelpers.web.stores import drive_folders as store

DAY = datetime.date(2026, 10, 21)
ROOT = "root-folder-id"


@pytest.fixture
def drive(monkeypatch) -> FakeDrive:
    fake = FakeDrive()
    fake.seed_folder("ALL ACCOUNTS", item_id=ROOT, shared=True)
    monkeypatch.setenv("OFM_GDRIVE_SERVICE_ACCOUNT", "{}")
    monkeypatch.setenv("OFM_GDRIVE_ROOT_FOLDER", ROOT)
    monkeypatch.setattr(drive_folders, "connect", lambda **_: fake)
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


def _account(drive: FakeDrive, handle: str, owner: str = "Anna") -> str:
    (account,) = drive.named(f"{handle} - {owner}", ROOT)
    return account


def _day_folder(
    drive: FakeDrive, handle: str, owner: str = "Anna", day: datetime.date = DAY
) -> str:
    (folder,) = drive.named(day_folder_name(day), _account(drive, handle, owner))
    return folder


# --- names ---------------------------------------------------------------------


def test_day_folder_is_the_iso_date():
    assert day_folder_name(datetime.date(2026, 1, 1)) == "2026-01-01"
    assert day_folder_name(DAY) == "2026-10-21"


def test_today_follows_the_configured_timezone(monkeypatch):
    monkeypatch.setenv("OFM_GDRIVE_TIMEZONE", "Pacific/Kiritimati")
    east = drive_folders.today()
    monkeypatch.setenv("OFM_GDRIVE_TIMEZONE", "Pacific/Pago_Pago")
    west = drive_folders.today()
    assert east - west == datetime.timedelta(days=1)


# Warsaw: CEST (UTC+2) until 03:00 on 2026-10-25, the last Sunday of
# October, then CET (UTC+1).
@pytest.mark.parametrize(
    ("utc", "day"),
    [
        ("2026-10-21T21:59:00", "2026-10-21"),  # 23:59 CEST
        ("2026-10-21T22:01:00", "2026-10-22"),  # 00:01 CEST
        ("2026-10-24T21:59:00", "2026-10-24"),  # 23:59 CEST, the night of the change
        ("2026-10-24T22:01:00", "2026-10-25"),  # 00:01 CEST, Sunday
        ("2026-10-25T22:59:00", "2026-10-25"),  # 23:59 CET, the first CET night
        ("2026-10-25T23:01:00", "2026-10-26"),  # 00:01 CET
    ],
)
def test_the_day_turns_at_warsaw_midnight_across_the_dst_change(utc, day):
    instant = datetime.datetime.fromisoformat(utc).replace(tzinfo=datetime.UTC)
    with time_machine.travel(instant, tick=False):
        assert drive_folders.today().isoformat() == day


def test_sync_at_warsaw_midnight_makes_the_new_days_folder(monkeypatch):
    fake = FakeDrive()
    fake.seed_folder("ALL ACCOUNTS", item_id=ROOT, shared=True)
    monkeypatch.setenv("OFM_GDRIVE_SERVICE_ACCOUNT", "{}")
    monkeypatch.setenv("OFM_GDRIVE_ROOT_FOLDER", ROOT)
    monkeypatch.setattr(drive_folders, "connect", lambda **_: fake)
    _login(_phone("Phone 01"), "instagram", "ann.ig")
    before = datetime.datetime(2026, 10, 24, 21, 59, tzinfo=datetime.UTC)
    with time_machine.travel(before, tick=False):
        drive_folders.sync()
    with time_machine.travel(before + datetime.timedelta(minutes=2), tick=False):
        assert drive_folders.sync().days == 1
    account = _account(fake, "ann.ig")
    assert fake.named("2026-10-24", account)
    assert fake.named("2026-10-25", account)


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
    assert drive.named("bob.ig - Bob", ROOT) == []
    # The bot owns what it made.
    assert drive.item(_account(drive, "ann.ig")).owner == FAKE_BOT_EMAIL


def test_second_sync_the_same_day_never_calls_drive(drive):
    _login(_phone("Phone 01"), "instagram", "ann.ig")
    drive_folders.sync()
    drive.clear_calls()

    report = drive_folders.sync()

    assert drive.calls == []
    assert report.created == []
    assert report.days == 0


def test_next_day_adds_only_the_day_folder(drive):
    _login(_phone("Phone 01"), "instagram", "ann.ig")
    drive_folders.sync()
    tomorrow = DAY + datetime.timedelta(days=1)
    drive.clear_calls()

    report = drive_folders.sync(tomorrow)

    assert report.created == []
    assert report.days == 1
    assert drive.writes == ["create"]
    assert _day_folder(drive, "ann.ig", day=tomorrow)


def test_hand_made_folders_are_reused_not_duplicated(drive):
    _login(_phone("Phone 01"), "instagram", "ann.ig")
    account = drive.seed_folder("ann.ig - Anna", ROOT)
    day = drive.seed_folder("2026-10-21", account)

    report = drive_folders.sync()

    assert report.adopted == ["ann.ig - Anna"]
    assert report.created == []
    assert drive.writes == []
    assert drive.named("ann.ig - Anna", ROOT) == [account]
    assert store.day_folders(DAY) == {store.targets()[0].login_id: day}


def test_last_years_day_folder_is_never_reused(drive):
    """With the year in the name, 2025's folder (and an old-style "Oct 21")
    is left alone and this year's is made."""
    _login(_phone("Phone 01"), "instagram", "ann.ig")
    account = drive.seed_folder("ann.ig - Anna", ROOT)
    last_year = drive.seed_folder("2025-10-21", account)
    old_style = drive.seed_folder("Oct 21", account)

    drive_folders.sync()

    made = _day_folder(drive, "ann.ig")
    assert made not in (last_year, old_style)
    assert drive.item(last_year).name == "2025-10-21"
    assert drive.item(old_style).name == "Oct 21"


def test_names_are_not_unique_and_the_oldest_is_adopted(drive):
    _login(_phone("Phone 01"), "instagram", "ann.ig")
    oldest = drive.seed_folder("ann.ig - Anna", ROOT)
    drive.seed_folder("ann.ig - Anna", ROOT)

    drive_folders.sync()

    assert store.account_folders()[store.targets()[0].login_id].folder_id == oldest


def test_vas_near_miss_folders_are_left_alone(drive):
    _login(_phone("Phone 01"), "instagram", "ann.ig")
    near = [
        drive.seed_folder(name, ROOT)
        for name in ("ann.ig-Anna", "Ann.ig - anna", "ann.ig - Anna ", "ann.ig")
    ]

    report = drive_folders.sync()

    assert report.created == ["ann.ig - Anna"]
    assert report.renamed == []
    assert [drive.item(i).name for i in near] == [
        "ann.ig-Anna",
        "Ann.ig - anna",
        "ann.ig - Anna ",
        "ann.ig",
    ]
    assert drive.named("2026-10-21", near[0]) == []


def test_new_owner_renames_the_folder(drive):
    phone = _phone("Phone 01", owner="Anna")
    _login(phone, "instagram", "ann.ig")
    drive_folders.sync()
    account = _account(drive, "ann.ig")
    devices.update_device(
        phone,
        DeviceFields(
            label="Phone 01", model_id="iphone-13", color_id="blue", owner="Kate"
        ),
    )

    report = drive_folders.sync()

    assert report.renamed == ["ann.ig - Kate"]
    assert drive.named("ann.ig - Kate", ROOT) == [account]
    assert report.days == 0


def test_a_hand_renamed_folder_stays_linked_by_id(drive):
    _login(_phone("Phone 01"), "instagram", "ann.ig")
    drive_folders.sync()
    account = _account(drive, "ann.ig")
    drive.rename_by_hand(account, "ANN (do not touch)")

    tomorrow = DAY + datetime.timedelta(days=1)
    report = drive_folders.sync(tomorrow)

    # Same folder: the next day goes inside it, and its name comes back.
    assert report.created == []
    assert report.renamed == ["ann.ig - Anna"]
    assert drive.named("ann.ig - Anna", ROOT) == [account]
    assert drive.named("2026-10-22", account)


def test_trashed_folder_is_made_again(drive):
    _login(_phone("Phone 01"), "instagram", "ann.ig")
    drive_folders.sync()
    account = _account(drive, "ann.ig")
    drive.trash(account)
    store.forget_day_folder(store.targets()[0].login_id, DAY)

    report = drive_folders.sync()

    assert report.created == ["ann.ig - Anna"]
    assert report.days == 1
    assert _account(drive, "ann.ig") != account
    # Trash is not delete: the old one is still there, in the trash.
    assert drive.item(account).trashed


def test_deleted_folder_is_made_again(drive):
    _login(_phone("Phone 01"), "instagram", "ann.ig")
    drive_folders.sync()
    drive.delete(_account(drive, "ann.ig"))
    store.forget_day_folder(store.targets()[0].login_id, DAY)

    report = drive_folders.sync()

    assert report.created == ["ann.ig - Anna"]
    assert _day_folder(drive, "ann.ig")


def test_one_failing_login_does_not_stop_the_others(drive):
    _login(_phone("Phone 01"), "instagram", "ann.ig")
    _login(_phone("Phone 02", owner="Bob"), "instagram", "bob.ig")
    drive.fail_on("ann.ig - Anna")

    report = drive_folders.sync()

    assert len(report.failed) == 1
    assert "ann.ig - Anna" in report.failed[0]
    assert _day_folder(drive, "bob.ig", owner="Bob")
    # Retried on the next sweep.
    drive.fail_on()
    assert drive_folders.sync().created == ["ann.ig - Anna"]


def test_a_login_on_two_phones_gets_one_folder(drive):
    first = _phone("Phone 01", owner="Anna")
    second = _phone("Phone 02", owner="Bob")
    _login(first, "instagram", "ann.ig")
    _login(second, "instagram", "ann.ig")

    assert drive_folders.sync().created == ["ann.ig - Anna"]


def test_root_not_shared_raises_and_changes_nothing(drive):
    _login(_phone("Phone 01"), "instagram", "ann.ig")
    drive.unshare(ROOT)
    with pytest.raises(FolderGoneError):
        drive_folders.sync()
    assert store.account_folders() == {}


def test_deleting_the_login_forgets_its_folders(drive):
    phone = _phone("Phone 01")
    login = _login(phone, "instagram", "ann.ig")
    drive_folders.sync()
    devices.remove_login(phone, login)
    assert store.account_folders() == {}
    assert store.day_folders(DAY) == {}
    # Drive keeps the folder and what is in it.
    assert drive.named("ann.ig - Anna", ROOT)


# --- concurrency -------------------------------------------------------------------


def _three_logins() -> None:
    for i, owner in enumerate(("Anna", "Bob", "Cleo")):
        _login(_phone(f"Phone 0{i}", owner=owner), "instagram", f"h{i}.ig")


def _assert_one_of_each(drive: FakeDrive) -> None:
    for i, owner in enumerate(("Anna", "Bob", "Cleo")):
        (account,) = drive.named(f"h{i}.ig - {owner}", ROOT)
        assert len(drive.named("2026-10-21", account)) == 1


def test_a_sync_during_another_does_nothing(drive, monkeypatch):
    """The button pressed while the loop's sweep is half-way: the second
    one waits for nothing and touches nothing."""
    _three_logins()
    inside = threading.Event()
    release = threading.Event()
    real_create = drive.create_folder

    def slow_create(parent: str, name: str):
        inside.set()
        assert release.wait(10)
        return real_create(parent, name)

    monkeypatch.setattr(drive, "create_folder", slow_create)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(drive_folders.sync)
        assert inside.wait(10)
        second = pool.submit(drive_folders.sync).result(timeout=10)
        release.set()
        done = first.result(timeout=10)

    assert second.busy
    assert second.created == []
    assert len(done.created) == 3
    _assert_one_of_each(drive)


def test_concurrent_syncs_never_duplicate_a_folder(drive):
    _three_logins()
    start = threading.Barrier(4)

    def racer():
        start.wait(timeout=10)
        return drive_folders.sync()

    with ThreadPoolExecutor(max_workers=4) as pool:
        reports = [f.result(timeout=30) for f in [pool.submit(racer) for _ in range(4)]]

    assert sum(len(r.created) for r in reports) == 3
    assert sum(r.days for r in reports) == 3
    _assert_one_of_each(drive)


# --- the live folder outside production ----------------------------------------------


@pytest.fixture
def live_root(drive, monkeypatch) -> FakeDrive:
    monkeypatch.setenv("OFM_GDRIVE_PRODUCTION_ROOT_FOLDER", ROOT)
    monkeypatch.delenv("OFM_ENV", raising=False)
    return drive


def test_sync_refuses_the_production_folder_outside_production(live_root):
    _login(_phone("Phone 01"), "instagram", "ann.ig")
    with pytest.raises(ProductionFolderError, match="sandbox"):
        drive_folders.sync()
    assert live_root.calls == []
    assert store.account_folders() == {}


def test_the_loop_does_not_start_on_the_production_folder(live_root):
    _login(_phone("Phone 01"), "instagram", "ann.ig")
    # Returns at once instead of looping forever. Its own thread: under
    # xdist this worker may also run e2e, whose Playwright loop is running.
    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(
            asyncio.run, asyncio.wait_for(drive_folders.drive_loop(), timeout=5)
        ).result(timeout=10)
    assert live_root.calls == []


def test_page_says_why_on_the_production_folder(live_root):
    html = _client().get("/drive").text
    assert "Refusing the production Drive folder outside production" in html
    assert live_root.calls == []


def test_production_may_use_the_production_folder(live_root, monkeypatch):
    monkeypatch.setenv("OFM_ENV", "production")
    _login(_phone("Phone 01"), "instagram", "ann.ig")
    assert drive_folders.sync().created == ["ann.ig - Anna"]


def test_another_folder_is_fine_outside_production(drive, monkeypatch):
    monkeypatch.setenv("OFM_GDRIVE_PRODUCTION_ROOT_FOLDER", "the-live-one")
    _login(_phone("Phone 01"), "instagram", "ann.ig")
    assert drive_folders.sync().created == ["ann.ig - Anna"]


# --- page ----------------------------------------------------------------------


def test_page_shows_today_files_with_previews(drive):
    _login(_phone("Phone 01"), "instagram", "ann.ig")
    _login(_phone("Phone 02", owner="Bob"), "instagram", "bob.ig")
    drive_folders.sync()
    day = _day_folder(drive, "ann.ig")
    video = drive.add_file(day, "reel1.mp4", "video/mp4", thumbnail=True)
    drive.add_file(day, "notes.txt", "text/plain")
    drive.seed_folder("old messy folder", ROOT)

    html = _client().get("/drive").text

    assert "Drive · 2026-10-21" in html
    assert "1 of 2 accounts uploaded today · 2 files" in html
    assert f'src="/drive/thumb/{video}"' in html
    assert "notes.txt" in html
    assert "Nothing uploaded today" in html
    assert "old messy folder" in html
    assert "ann.ig - Anna" not in html.split("other folder")[-1]


def test_page_without_setup_says_what_is_missing(monkeypatch):
    monkeypatch.delenv("OFM_GDRIVE_SERVICE_ACCOUNT", raising=False)
    monkeypatch.delenv("OFM_GDRIVE_ROOT_FOLDER", raising=False)
    html = _client().get("/drive").text
    assert (
        "Google Drive is not set up: OFM_GDRIVE_SERVICE_ACCOUNT and"
        " OFM_GDRIVE_ROOT_FOLDER are empty in .env." in html
    )
    assert 'id="dr-sync" disabled' in html


def test_page_without_a_root_folder_is_not_configured(monkeypatch):
    """Dev's default now: a key, no folder."""
    monkeypatch.setenv("OFM_GDRIVE_SERVICE_ACCOUNT", "{}")
    monkeypatch.setenv("OFM_GDRIVE_ROOT_FOLDER", "")
    response = _client().get("/drive")
    assert response.status_code == 200
    assert (
        "Google Drive is not set up: OFM_GDRIVE_ROOT_FOLDER is empty in .env."
        in response.text
    )


def test_page_without_a_key_names_only_the_key(monkeypatch):
    monkeypatch.delenv("OFM_GDRIVE_SERVICE_ACCOUNT", raising=False)
    monkeypatch.setenv("OFM_GDRIVE_ROOT_FOLDER", "some-folder")
    html = _client().get("/drive").text
    assert (
        "Google Drive is not set up: OFM_GDRIVE_SERVICE_ACCOUNT is empty in .env."
        in html
    )


def test_page_not_shared_names_the_bot(drive):
    _login(_phone("Phone 01"), "instagram", "ann.ig")
    drive.unshare(ROOT)
    html = _client().get("/drive").text
    assert "share it with bot@x.iam as Editor" in html


def test_page_forgets_a_deleted_day_folder(drive):
    _login(_phone("Phone 01"), "instagram", "ann.ig")
    drive_folders.sync()
    drive.trash(_day_folder(drive, "ann.ig"))

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
    drive.unshare(ROOT)
    response = _client().post("/drive/sync")
    assert response.status_code == 502
    assert "not found" in response.json()["detail"]


def test_sync_route_on_the_production_folder_is_502(live_root):
    response = _client().post("/drive/sync")
    assert response.status_code == 502
    assert "Refusing the production Drive folder" in response.json()["detail"]


def _uploaded_preview(drive: FakeDrive) -> str:
    _login(_phone("Phone 01"), "instagram", "ann.ig")
    drive_folders.sync()
    return drive.add_file(_day_folder(drive, "ann.ig"), "a.jpg", thumbnail=True)


def test_thumb_route_serves_a_day_folder_file_and_caches_privately(drive):
    file_id = _uploaded_preview(drive)
    response = _client().get(f"/drive/thumb/{file_id}")
    assert response.status_code == 200
    assert response.content.startswith(b"\xff\xd8")
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["cache-control"] == "private, max-age=86400"


def test_thumb_route_without_preview_is_404(drive):
    _login(_phone("Phone 01"), "instagram", "ann.ig")
    drive_folders.sync()
    file_id = drive.add_file(_day_folder(drive, "ann.ig"), "a.txt", "text/plain")
    assert _client().get(f"/drive/thumb/{file_id}").status_code == 404


def test_thumb_route_refuses_a_random_id(drive):
    _uploaded_preview(drive)
    response = _client().get("/drive/thumb/RandomFileId0123456789")
    assert response.status_code == 404
    assert "thumbnail" not in drive.calls


def test_thumb_route_refuses_a_file_outside_the_apps_folders(drive):
    """The bot sees the whole root: a file the VA keeps elsewhere in it
    (or anywhere else shared with the bot) is not the page's to show."""
    _uploaded_preview(drive)
    loose = drive.add_file(ROOT, "passport.jpg", thumbnail=True)
    elsewhere = drive.seed_folder("Someone else's", shared=True)
    private = drive.add_file(elsewhere, "private.jpg", thumbnail=True)

    for file_id in (loose, private):
        assert _client().get(f"/drive/thumb/{file_id}").status_code == 404
    assert "thumbnail" not in drive.calls


def test_thumb_route_refuses_a_trashed_file(drive):
    file_id = _uploaded_preview(drive)
    drive.trash(file_id)
    assert _client().get(f"/drive/thumb/{file_id}").status_code == 404


def test_thumb_route_rejects_a_bad_id(drive):
    assert _client().get("/drive/thumb/..%2Fetc").status_code in (404, 422)
    assert drive.calls == []


def test_thumb_route_needs_login(drive):
    file_id = _uploaded_preview(drive)
    response = TestClient(app).get(f"/drive/thumb/{file_id}", follow_redirects=False)
    assert response.status_code == 303


# --- the fakes switch ---------------------------------------------------------------


def test_fake_externals_connects_to_the_fake_drive(monkeypatch, tmp_path):
    monkeypatch.setenv("OFM_FAKE_EXTERNALS", "1")
    monkeypatch.setenv("OFM_FAKES_DIR", str(tmp_path))
    monkeypatch.setenv("OFM_GDRIVE_ROOT_FOLDER", "fake-root")
    drive = connect()
    assert isinstance(drive, FakeDrive)
    assert drive.folder("fake-root").name == "ALL ACCOUNTS"
    # Shared through the file: another process sees the same Drive.
    made = drive.create_folder("fake-root", "x")
    assert FakeDrive(tmp_path).named("x", "fake-root") == [made.id]


# --- a different root folder -----------------------------------------------------------


def test_folders_of_another_root_are_never_written_into_or_shown(drive, monkeypatch):
    """Dev once pointed at the live folder: once the root is a sandbox,
    the folders saved under the live one are ignored -- the sweep makes the
    sandbox's own, and nothing more is written into the old ones."""
    _login(_phone("Phone 01"), "instagram", "ann.ig")
    drive_folders.sync()
    old_account = _account(drive, "ann.ig")
    old_day = _day_folder(drive, "ann.ig")
    preview = drive.add_file(old_day, "live.jpg", thumbnail=True)
    sandbox = drive.seed_folder("SANDBOX", shared=True)
    monkeypatch.setenv("OFM_GDRIVE_ROOT_FOLDER", sandbox)

    assert store.account_folders() == {}
    assert store.day_folders(DAY) == {}
    assert "live.jpg" not in _client().get("/drive").text
    assert _client().get(f"/drive/thumb/{preview}").status_code == 404

    tomorrow = DAY + datetime.timedelta(days=1)
    report = drive_folders.sync(tomorrow)

    assert report.created == ["ann.ig - Anna"]
    (new_account,) = drive.named("ann.ig - Anna", sandbox)
    assert drive.named("2026-10-22", new_account)
    assert drive.named("2026-10-22", old_account) == []
    assert drive.item(old_account).name == "ann.ig - Anna"


def test_cards_are_ordered_by_the_accounts_followers(drive):
    from ofmhelpers.socials.models import AccountProfile, AccountStats
    from ofmhelpers.web.stores import tracked_accounts

    for label, handle, followers in (
        ("Phone 01", "small.ig", 500),
        ("Phone 02", "big.ig", 90_000),
        ("Phone 03", "new.ig", None),
    ):
        _login(_phone(label, owner=label[-2:]), "instagram", handle)
        if followers is not None:
            tracked_accounts.track("instagram", handle)
            (account,) = [
                a for a in tracked_accounts.list_accounts() if a.handle == handle
            ]
            tracked_accounts.record_scan(
                account.id,
                AccountStats(
                    profile=AccountProfile(handle=handle, followers=followers),
                    posts=[],
                    scanned_at=1.0,
                ),
            )

    assert [t.handle for t in store.targets()] == ["big.ig", "small.ig", "new.ig"]
    html = _client().get("/drive").text
    order = [html.index(f"<h2>{h}</h2>") for h in ("big.ig", "small.ig", "new.ig")]
    assert order == sorted(order)
    assert "90k" not in html
    assert "90000" not in html


def test_a_trashed_day_folder_that_still_lists_is_forgotten(drive):
    """Drive still lists a trashed folder's files; the page checks the
    folder itself, so the day is forgotten and made again."""
    _login(_phone("Phone 01"), "instagram", "ann.ig")
    drive_folders.sync()
    day = _day_folder(drive, "ann.ig")
    drive.add_file(day, "kept.jpg")
    drive.trash(day)
    assert [f.name for f in drive.children(day)] == ["kept.jpg"]

    html = _client().get("/drive").text

    assert "Today&#39;s folder was deleted" in html
    assert store.day_folders(DAY) == {}


def test_a_card_that_fails_to_load_says_so(drive, monkeypatch):
    _login(_phone("Phone 01"), "instagram", "ann.ig")
    drive_folders.sync()
    real_children = drive.children

    def children(parent_id, *, folders_only=False):
        if not folders_only:
            msg = "boom"
            raise RuntimeError(msg)
        return real_children(parent_id, folders_only=folders_only)

    monkeypatch.setattr(drive, "children", children)

    html = _client().get("/drive").text

    assert "Could not read this folder from Drive" in html
    assert "Nothing uploaded today" not in html
