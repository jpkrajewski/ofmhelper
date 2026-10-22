"""/drive: every Instagram login on an active phone gets "<handle> - <owner>"
in the shared Drive folder, which the VA uploads into, and a POSTED folder
inside it, which people move a video into once posted. Every sweep heals
whatever it finds: a re-run writes nothing, hand-made folders are reused, a
new owner renames, deleted / trashed / renamed / dragged-out folders come
back, concurrent sweeps never duplicate, the live folder is refused outside
production, nothing in Drive is ever moved or deleted. The page shows what
waits in each folder (previews proxied through the app, and only those) and
how much was posted: now, today, in 7 days."""

import asyncio
import datetime
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo

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
from ofmhelpers.web.schemas import DeviceFields, LoginFields
from ofmhelpers.web.stores import devices
from ofmhelpers.web.stores import drive_folders as store

ROOT = "root-folder-id"
POSTED = "POSTED"


@pytest.fixture
def drive(monkeypatch) -> FakeDrive:
    fake = FakeDrive()
    fake.seed_folder("ALL ACCOUNTS", item_id=ROOT, shared=True)
    monkeypatch.setenv("OFM_GDRIVE_SERVICE_ACCOUNT", "{}")
    monkeypatch.setenv("OFM_GDRIVE_ROOT_FOLDER", ROOT)
    monkeypatch.setattr(drive_folders, "connect", lambda **_: fake)
    monkeypatch.setattr(drive_folders, "service_account_email", lambda: "bot@x.iam")
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


def _posted(drive: FakeDrive, handle: str, owner: str = "Anna") -> str:
    (posted,) = drive.named(POSTED, _account(drive, handle, owner))
    return posted


def _stored(handle: str = "ann.ig"):
    (target,) = [t for t in store.targets() if t.handle == handle]
    return store.account_folders().get(target.login_id)


# --- sync: account folder + POSTED ----------------------------------------------


def test_sync_makes_a_folder_and_its_posted_folder_per_instagram_login(drive):
    phone = _phone("Phone 01", owner="Anna")
    _login(phone, "instagram", "ann.ig")
    _login(phone, "gmail", "a@gmail.com")
    retired = _phone("Phone 02", owner="Bob")
    _login(retired, "instagram", "bob.ig")
    devices.retire_device(retired)

    report = drive_folders.sync()

    assert report.created == ["ann.ig - Anna"]
    assert report.posted_folders == ["ann.ig - Anna/POSTED"]
    assert not report.failed
    account = _account(drive, "ann.ig")
    assert drive.named(POSTED, account) == [_stored().posted_folder_id]
    # Nothing else: no day folder.
    assert [f.name for f in drive.children(account)] == [POSTED]
    assert drive.named("bob.ig - Bob", ROOT) == []
    # The bot owns what it made.
    assert drive.item(account).owner == FAKE_BOT_EMAIL


def test_second_sync_writes_nothing(drive):
    _login(_phone("Phone 01"), "instagram", "ann.ig")
    drive_folders.sync()
    drive.clear_calls()

    report = drive_folders.sync()

    assert drive.writes == []
    assert report.created == report.posted_folders == report.renamed == []


def test_hand_made_folders_are_reused_not_duplicated(drive):
    _login(_phone("Phone 01"), "instagram", "ann.ig")
    account = drive.seed_folder("ann.ig - Anna", ROOT)
    posted = drive.seed_folder(POSTED, account)

    report = drive_folders.sync()

    assert report.adopted == ["ann.ig - Anna"]
    assert report.created == []
    assert drive.writes == []
    assert _stored().posted_folder_id == posted


@pytest.mark.parametrize("name", ["Posted", "posted ", " POSTED"])
def test_a_hand_made_posted_folder_of_any_case_is_adopted_and_named_right(drive, name):
    _login(_phone("Phone 01"), "instagram", "ann.ig")
    account = drive.seed_folder("ann.ig - Anna", ROOT)
    posted = drive.seed_folder(name, account)

    report = drive_folders.sync()

    assert _stored().posted_folder_id == posted
    assert drive.item(posted).name == POSTED
    assert report.renamed == ["ann.ig - Anna/POSTED"]
    assert drive.writes == ["rename"]


def test_names_are_not_unique_and_the_oldest_is_adopted(drive):
    _login(_phone("Phone 01"), "instagram", "ann.ig")
    oldest = drive.seed_folder("ann.ig - Anna", ROOT)
    drive.seed_folder("ann.ig - Anna", ROOT)
    first_posted = drive.seed_folder(POSTED, oldest)
    drive.seed_folder(POSTED, oldest)

    drive_folders.sync()

    assert _stored().folder_id == oldest
    assert _stored().posted_folder_id == first_posted


def test_vas_near_miss_folders_are_left_alone(drive):
    _login(_phone("Phone 01"), "instagram", "ann.ig")
    near = [
        drive.seed_folder(name, ROOT)
        for name in ("ann.ig-Anna", "Ann.ig - anna", "ann.ig - Anna ", "ann.ig")
    ]

    report = drive_folders.sync()

    assert report.created == ["ann.ig - Anna"]
    assert [drive.item(i).name for i in near] == [
        "ann.ig-Anna",
        "Ann.ig - anna",
        "ann.ig - Anna ",
        "ann.ig",
    ]
    assert all(drive.children(i) == [] for i in near)


def test_new_owner_renames_the_folder_and_keeps_its_posted_folder(drive):
    phone = _phone("Phone 01", owner="Anna")
    _login(phone, "instagram", "ann.ig")
    drive_folders.sync()
    account = _account(drive, "ann.ig")
    posted = _posted(drive, "ann.ig")
    devices.update_device(
        phone,
        DeviceFields(
            label="Phone 01", model_id="iphone-13", color_id="blue", owner="Kate"
        ),
    )

    report = drive_folders.sync()

    assert report.renamed == ["ann.ig - Kate"]
    assert drive.named("ann.ig - Kate", ROOT) == [account]
    assert _stored().posted_folder_id == posted
    assert report.posted_folders == []


def test_a_hand_renamed_account_folder_gets_its_name_back(drive):
    _login(_phone("Phone 01"), "instagram", "ann.ig")
    drive_folders.sync()
    account = _account(drive, "ann.ig")
    drive.rename_by_hand(account, "ANN (do not touch)")

    report = drive_folders.sync()

    # Same folder, kept by id: its name comes back, nothing new is made.
    assert report.renamed == ["ann.ig - Anna"]
    assert report.created == []
    assert drive.named("ann.ig - Anna", ROOT) == [account]


# --- self-healing: POSTED -------------------------------------------------------


def test_a_trashed_posted_folder_is_made_again_in_the_same_sweep(drive):
    _login(_phone("Phone 01"), "instagram", "ann.ig")
    drive_folders.sync()
    old = _posted(drive, "ann.ig")
    drive.trash(old)

    report = drive_folders.sync()

    new = _stored().posted_folder_id
    assert new != old
    assert drive.named(POSTED, _account(drive, "ann.ig")) == [new]
    assert report.posted_folders == ["ann.ig - Anna/POSTED"]
    # Trash is not delete: the old one is still there, in the trash.
    assert drive.item(old).trashed


def test_a_deleted_posted_folder_is_made_again(drive):
    _login(_phone("Phone 01"), "instagram", "ann.ig")
    drive_folders.sync()
    drive.delete(_posted(drive, "ann.ig"))

    drive_folders.sync()

    assert drive.named(POSTED, _account(drive, "ann.ig")) == [
        _stored().posted_folder_id
    ]


def test_a_hand_renamed_posted_folder_gets_its_name_back(drive):
    _login(_phone("Phone 01"), "instagram", "ann.ig")
    drive_folders.sync()
    posted = _posted(drive, "ann.ig")
    drive.rename_by_hand(posted, "done")

    report = drive_folders.sync()

    assert drive.item(posted).name == POSTED
    assert _stored().posted_folder_id == posted
    assert report.renamed == ["ann.ig - Anna/POSTED"]


def test_a_posted_folder_dragged_out_is_left_there_and_a_new_one_made(drive):
    _login(_phone("Phone 01"), "instagram", "ann.ig")
    drive_folders.sync()
    posted = _posted(drive, "ann.ig")
    drive.move(posted, ROOT)

    drive_folders.sync()

    new = _stored().posted_folder_id
    assert new != posted
    assert drive.named(POSTED, _account(drive, "ann.ig")) == [new]
    # Never moved back by the app: it stays where the person put it.
    assert drive.item(posted).parents == [ROOT]


def test_a_trashed_account_folder_is_made_again_with_its_posted_folder(drive):
    _login(_phone("Phone 01"), "instagram", "ann.ig")
    drive_folders.sync()
    account = _account(drive, "ann.ig")
    drive.trash(account)

    report = drive_folders.sync()

    assert report.created == ["ann.ig - Anna"]
    new = _account(drive, "ann.ig")
    assert new != account
    assert drive.named(POSTED, new) == [_stored().posted_folder_id]
    assert drive.item(account).trashed


def test_a_deleted_account_folder_is_made_again(drive):
    _login(_phone("Phone 01"), "instagram", "ann.ig")
    drive_folders.sync()
    drive.delete(_account(drive, "ann.ig"))

    report = drive_folders.sync()

    assert report.created == ["ann.ig - Anna"]
    assert _posted(drive, "ann.ig") == _stored().posted_folder_id


def test_state_before_posted_folders_heals_on_the_first_sweep(drive):
    """Production on deploy: account rows without a POSTED id, and that
    day's folder in Drive with uploads in it. The sweep adds POSTED and
    touches nothing else."""
    login = _login(_phone("Phone 01"), "instagram", "ann.ig")
    account = drive.seed_folder("ann.ig - Anna", ROOT, owner=FAKE_BOT_EMAIL)
    day = drive.seed_folder("2026-10-21", account, owner=FAKE_BOT_EMAIL)
    upload = drive.add_file(day, "reel.mp4", "video/mp4")
    store.save_account_folder(login, account, "ann.ig - Anna")
    assert store.account_folders()[login].posted_folder_id is None

    report = drive_folders.sync()

    assert report.created == report.adopted == []
    assert report.posted_folders == ["ann.ig - Anna/POSTED"]
    assert drive.writes == ["create"]
    assert drive.item(upload).parents == [day]
    assert drive.item(day).name == "2026-10-21"


def test_a_new_account_folder_drops_the_old_posted_id(drive):
    login = _login(_phone("Phone 01"), "instagram", "ann.ig")
    store.save_account_folder(login, "folder-a", "ann.ig - Anna")
    store.save_posted_folder(login, "posted-a")
    store.save_account_folder(login, "folder-a", "ann.ig - Kate")
    assert store.account_folders()[login].posted_folder_id == "posted-a"

    store.save_account_folder(login, "folder-b", "ann.ig - Anna")

    assert store.account_folders()[login].posted_folder_id is None


def test_one_failing_login_does_not_stop_the_others(drive):
    _login(_phone("Phone 01"), "instagram", "ann.ig")
    _login(_phone("Phone 02", owner="Bob"), "instagram", "bob.ig")
    drive.fail_on("ann.ig - Anna")

    report = drive_folders.sync()

    assert len(report.failed) == 1
    assert "ann.ig - Anna" in report.failed[0]
    assert _posted(drive, "bob.ig", owner="Bob")
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


def test_deleting_the_login_forgets_its_folders_and_history(drive):
    phone = _phone("Phone 01")
    login = _login(phone, "instagram", "ann.ig")
    drive_folders.sync()
    drive.add_file(_posted(drive, "ann.ig"), "reel.mp4", "video/mp4")
    drive_folders.sync()
    devices.remove_login(phone, login)
    assert store.account_folders() == {}
    assert store.posted_counts([0.0]) == {}
    # Drive keeps the folder and what is in it.
    assert drive.named("ann.ig - Anna", ROOT)


# --- tally: what was posted -----------------------------------------------------


def test_a_video_moved_into_posted_counts_once(drive):
    _login(_phone("Phone 01"), "instagram", "ann.ig")
    drive_folders.sync()
    reel = drive.add_file(_account(drive, "ann.ig"), "reel.mp4", "video/mp4")

    assert drive_folders.sync().newly_posted == 0
    drive.move(reel, _posted(drive, "ann.ig"))
    assert drive_folders.sync().newly_posted == 1
    assert drive_folders.sync().newly_posted == 0

    card = drive_folders.board().cards[0]
    assert (card.posted_total, card.posted_today, card.posted_week) == (1, 1, 1)
    assert card.waiting == []


def test_a_file_moved_back_out_keeps_its_history(drive):
    _login(_phone("Phone 01"), "instagram", "ann.ig")
    drive_folders.sync()
    reel = drive.add_file(_posted(drive, "ann.ig"), "reel.mp4", "video/mp4")
    drive_folders.sync()
    drive.move(reel, _account(drive, "ann.ig"))

    drive_folders.sync()

    card = drive_folders.board().cards[0]
    assert card.posted_total == 0
    assert card.posted_today == 1
    assert [f.id for f in card.waiting] == [reel]


def test_a_second_posted_folder_counts_and_is_flagged(drive):
    _login(_phone("Phone 01"), "instagram", "ann.ig")
    drive_folders.sync()
    extra = drive.seed_folder("posted", _account(drive, "ann.ig"))
    drive.add_file(extra, "a.mp4", "video/mp4")
    drive.add_file(_posted(drive, "ann.ig"), "b.mp4", "video/mp4")

    assert drive_folders.sync().newly_posted == 2

    card = drive_folders.board().cards[0]
    assert card.posted_total == 2
    assert [f.id for f in card.scan.extra_posted_folders] == [extra]
    html = _client().get("/drive").text
    assert '1 more "POSTED" folder in here' in html
    # Never renamed or moved: a person merges them.
    assert drive.item(extra).name == "posted"


def test_posted_today_turns_at_warsaw_midnight(drive):
    """23:59 and 00:01 Warsaw time (CEST, UTC+2) are different days; the
    week still has both."""
    _login(_phone("Phone 01"), "instagram", "ann.ig")
    drive_folders.sync()
    posted = _posted(drive, "ann.ig")
    late = datetime.datetime(2026, 10, 21, 21, 59, tzinfo=datetime.UTC)
    with time_machine.travel(late, tick=False):
        drive.add_file(posted, "late.mp4", "video/mp4")
        drive_folders.sync()
    with time_machine.travel(late + datetime.timedelta(minutes=2), tick=False):
        drive.add_file(posted, "early.mp4", "video/mp4")
        drive_folders.sync()
        card = drive_folders.board().cards[0]

    assert (card.posted_today, card.posted_week, card.posted_total) == (1, 2, 2)


def test_the_week_spans_the_dst_change():
    """Seven Warsaw days back from Monday 2026-10-26 (CET) start on Tuesday
    2026-10-20 at local midnight (CEST, UTC+2)."""
    at = datetime.datetime(2026, 10, 26, 12, 0, tzinfo=ZoneInfo("Europe/Warsaw"))
    today, week = drive_folders._period_starts(at)
    assert datetime.datetime.fromtimestamp(today, datetime.UTC).hour == 23  # CET
    start = datetime.datetime.fromtimestamp(week, datetime.UTC)
    assert (start.date(), start.hour) == (datetime.date(2026, 10, 19), 22)  # CEST


def test_one_account_failing_to_list_does_not_stop_the_tally(drive, monkeypatch):
    _login(_phone("Phone 01"), "instagram", "ann.ig")
    _login(_phone("Phone 02", owner="Bob"), "instagram", "bob.ig")
    drive_folders.sync()
    ann_posted = _posted(drive, "ann.ig")
    drive.add_file(_posted(drive, "bob.ig", owner="Bob"), "b.mp4", "video/mp4")
    real_children = drive.children

    def children(parent_id, *, folders_only=False):
        if parent_id == ann_posted:
            msg = "Drive refused"
            raise drive_folders.DriveError(msg)
        return real_children(parent_id, folders_only=folders_only)

    monkeypatch.setattr(drive, "children", children)

    report = drive_folders.sync()

    assert report.newly_posted == 1
    assert len(report.failed) == 1
    assert "ann.ig - Anna" in report.failed[0]


# --- concurrency -------------------------------------------------------------------


def _three_logins() -> None:
    for i, owner in enumerate(("Anna", "Bob", "Cleo")):
        _login(_phone(f"Phone 0{i}", owner=owner), "instagram", f"h{i}.ig")


def _assert_one_of_each(drive: FakeDrive) -> None:
    for i, owner in enumerate(("Anna", "Bob", "Cleo")):
        (account,) = drive.named(f"h{i}.ig - {owner}", ROOT)
        assert len(drive.named(POSTED, account)) == 1


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
    assert sum(len(r.posted_folders) for r in reports) == 3
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


def test_page_shows_what_waits_and_what_was_posted(drive):
    _login(_phone("Phone 01"), "instagram", "ann.ig")
    _login(_phone("Phone 02", owner="Bob"), "instagram", "bob.ig")
    drive_folders.sync()
    account = _account(drive, "ann.ig")
    video = drive.add_file(account, "reel1.mp4", "video/mp4", thumbnail=True)
    drive.add_file(account, "notes.txt", "text/plain")
    drive.add_file(_posted(drive, "ann.ig"), "old.mp4", "video/mp4")
    drive.seed_folder("old messy folder", ROOT)
    drive_folders.sync()

    html = _client().get("/drive").text

    assert (
        "2 files waiting to post in 1 of 2 accounts · Posted today: 1 · Last 7 days: 1"
        in html
    )
    assert f'src="/drive/thumb/{video}"' in html
    assert "notes.txt" in html
    assert "Nothing waiting to post" in html
    assert "old messy folder" in html
    assert "ann.ig - Anna" not in html.split("other folder")[-1]


def test_other_folders_in_an_account_folder_are_ignored(drive):
    """The old day folders: VAs clear them out; the page neither shows nor
    lists nor touches them."""
    _login(_phone("Phone 01"), "instagram", "ann.ig")
    drive_folders.sync()
    day = drive.seed_folder("2026-10-21", _account(drive, "ann.ig"))
    drive.add_file(day, "a.mp4", "video/mp4")
    drive.clear_calls()

    html = _client().get("/drive").text

    assert "2026-10-21" not in html
    assert "Nothing waiting to post" in html
    assert drive.writes == []
    assert drive.calls.count("children") == 3  # root, account, POSTED


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


def test_page_forgets_a_trashed_account_folder_that_still_lists(drive):
    """Drive still lists a trashed folder's files; the page checks the
    folder itself, so it is forgotten and the next sweep makes it again."""
    _login(_phone("Phone 01"), "instagram", "ann.ig")
    drive_folders.sync()
    account = _account(drive, "ann.ig")
    drive.add_file(account, "kept.jpg")
    drive.trash(account)
    assert [f.name for f in drive.children(account) if f.kind != "folder"] == [
        "kept.jpg"
    ]

    html = _client().get("/drive").text

    assert "The account&#39;s folder was deleted" in html
    assert store.account_folders() == {}
    assert drive_folders.sync().created == ["ann.ig - Anna"]


def test_drive_is_in_the_nav():
    assert 'href="/drive"' in _client().get("/phones").text


def test_page_needs_login():
    response = TestClient(app).get("/drive", follow_redirects=False)
    assert response.status_code == 303


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
    assert "Nothing waiting to post" not in html


# --- routes --------------------------------------------------------------------


def test_sync_route_reports(drive):
    _login(_phone("Phone 01"), "instagram", "ann.ig")
    response = _client().post("/drive/sync")
    assert response.status_code == 200
    body = response.json()
    assert body["created"] == ["ann.ig - Anna"]
    assert body["posted_folders"] == ["ann.ig - Anna/POSTED"]
    assert body["newly_posted"] == 0


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
    return drive.add_file(_account(drive, "ann.ig"), "a.jpg", thumbnail=True)


def test_thumb_route_serves_an_account_folder_file_and_caches_privately(drive):
    file_id = _uploaded_preview(drive)
    response = _client().get(f"/drive/thumb/{file_id}")
    assert response.status_code == 200
    assert response.content.startswith(b"\xff\xd8")
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["cache-control"] == "private, max-age=86400"


def test_thumb_route_serves_a_posted_file(drive):
    _uploaded_preview(drive)
    posted = drive.add_file(_posted(drive, "ann.ig"), "p.jpg", thumbnail=True)
    assert _client().get(f"/drive/thumb/{posted}").status_code == 200


def test_thumb_route_without_preview_is_404(drive):
    _login(_phone("Phone 01"), "instagram", "ann.ig")
    drive_folders.sync()
    file_id = drive.add_file(_account(drive, "ann.ig"), "a.txt", "text/plain")
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
    preview = drive.add_file(old_account, "live.jpg", thumbnail=True)
    sandbox = drive.seed_folder("SANDBOX", shared=True)
    monkeypatch.setenv("OFM_GDRIVE_ROOT_FOLDER", sandbox)

    assert store.account_folders() == {}
    assert "live.jpg" not in _client().get("/drive").text
    assert _client().get(f"/drive/thumb/{preview}").status_code == 404
    drive.clear_calls()

    report = drive_folders.sync()

    assert report.created == ["ann.ig - Anna"]
    (new_account,) = drive.named("ann.ig - Anna", sandbox)
    assert drive.named(POSTED, new_account)
    assert len(drive.named(POSTED, old_account)) == 1
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
