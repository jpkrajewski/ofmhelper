import datetime
import threading
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo

import pytest
import time_machine
from fastapi.testclient import TestClient
from signin import make_user, sign_in, sign_in_agency
from sqlalchemy import text
from statements import counting

from ofmhelpers.db.devices import store as devices
from ofmhelpers.db.devices.models import DeviceFields, LoginFields
from ofmhelpers.db.devices.schema import AddedLogin
from ofmhelpers.db.drive_folders import store
from ofmhelpers.db.drive_folders.models import DriveBoard
from ofmhelpers.db.drive_folders.schema import VaFolder
from ofmhelpers.db.drive_uploads import store as upload_store
from ofmhelpers.db.session import session_scope
from ofmhelpers.db.tracked_accounts import store as tracked_accounts
from ofmhelpers.db.tracked_accounts.store import list_accounts, track
from ofmhelpers.db.users import store as users
from ofmhelpers.db.users.schema import UserForm
from ofmhelpers.features.storage import client as drive_client
from ofmhelpers.features.storage import folders as drive_folders
from ofmhelpers.features.storage import jobs as storage_jobs
from ofmhelpers.features.storage import posting
from ofmhelpers.features.storage.constants import StorageConstants
from ofmhelpers.integrations.socials.models import AccountProfile, AccountStats
from ofmhelpers.integrations.storage import DriveError, FolderGoneError
from ofmhelpers.integrations.storage.gdrive.fake import FakeDrive, fake_drive
from ofmhelpers.web.main import app

AGENCY = "agency@example.com"
MY_DRIVE = f"mydrive-{AGENCY}"
ROOT = "agency-root-folder"
POSTED = "POSTED"


@pytest.fixture
def drive(fake_externals, monkeypatch, tmp_path) -> FakeDrive:
    monkeypatch.setenv("OFM_UPLOADS_ROOT", str(tmp_path))
    fake = fake_drive()
    fake.seed_folder("My Drive", item_id=MY_DRIVE, owner=AGENCY, shared=True)
    fake.seed_folder("DEV - ALL ACCOUNTS", MY_DRIVE, item_id=ROOT, owner=AGENCY)
    users._repository().signed_in(users.agency_id(), "", f"fake-refresh:{AGENCY}")
    users.set_drive_root(users.agency_id(), ROOT)
    return fake


def _client() -> TestClient:
    c = TestClient(app)
    sign_in_agency(c)
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
    return added.id


def _account(drive: FakeDrive, handle: str, owner: str = "Anna") -> str:
    (account,) = drive.named(f"{handle} - {owner}", ROOT)
    return account


def _posted(drive: FakeDrive, handle: str, owner: str = "Anna") -> str:
    (posted,) = drive.named(POSTED, _account(drive, handle, owner))
    return posted


def _board() -> DriveBoard:
    owner = users.agency_id()
    return drive_client.storage_of(owner, interactive=True).folders.board()


def _stored(handle: str = "ann.ig"):
    (target,) = [t for t in store.targets() if t.handle == handle]
    return store.account_folders().get(target.login_id)


def test_sync_makes_a_folder_and_its_posted_folder_per_instagram_login(drive):
    phone = _phone("Phone 01", owner="Anna")
    _login(phone, "instagram", "ann.ig")
    _login(phone, "gmail", "a@gmail.com")
    retired = _phone("Phone 02", owner="Bob")
    _login(retired, "instagram", "bob.ig")
    devices.retire_device(retired)

    report = storage_jobs.sync()

    assert report.created == ["ann.ig - Anna"]
    assert report.posted_folders == ["ann.ig - Anna/POSTED"]
    assert not report.failed
    account = _account(drive, "ann.ig")
    assert drive.named(POSTED, account) == [_stored().posted_folder_id]
    assert [f.name for f in drive.children(account)] == [POSTED]
    assert drive.named("bob.ig - Bob", ROOT) == []
    assert drive.item(account).owner == AGENCY


def test_second_sync_writes_nothing(drive):
    _login(_phone("Phone 01"), "instagram", "ann.ig")
    storage_jobs.sync()
    drive.clear_calls()

    report = storage_jobs.sync()

    assert drive.writes == []
    assert report.created == report.posted_folders == report.renamed == []


def test_hand_made_folders_are_reused_not_duplicated(drive):
    _login(_phone("Phone 01"), "instagram", "ann.ig")
    account = drive.seed_folder("ann.ig - Anna", ROOT)
    posted = drive.seed_folder(POSTED, account)

    report = storage_jobs.sync()

    assert report.adopted == ["ann.ig - Anna"]
    assert report.created == []
    assert drive.writes == []
    assert _stored().posted_folder_id == posted


@pytest.mark.parametrize("name", ["Posted", "posted ", " POSTED"])
def test_a_hand_made_posted_folder_of_any_case_is_adopted_and_named_right(drive, name):
    _login(_phone("Phone 01"), "instagram", "ann.ig")
    account = drive.seed_folder("ann.ig - Anna", ROOT)
    posted = drive.seed_folder(name, account)

    report = storage_jobs.sync()

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

    storage_jobs.sync()

    assert _stored().folder_id == oldest
    assert _stored().posted_folder_id == first_posted


def test_vas_near_miss_folders_are_left_alone(drive):
    _login(_phone("Phone 01"), "instagram", "ann.ig")
    near = [
        drive.seed_folder(name, ROOT)
        for name in ("ann.ig-Anna", "Ann.ig - anna", "ann.ig - Anna ", "ann.ig")
    ]

    report = storage_jobs.sync()

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
    storage_jobs.sync()
    account = _account(drive, "ann.ig")
    posted = _posted(drive, "ann.ig")
    devices.update_device(
        phone,
        DeviceFields(
            label="Phone 01", model_id="iphone-13", color_id="blue", owner="Kate"
        ),
    )

    report = storage_jobs.sync()

    assert report.renamed == ["ann.ig - Kate"]
    assert drive.named("ann.ig - Kate", ROOT) == [account]
    assert _stored().posted_folder_id == posted
    assert report.posted_folders == []


def test_a_hand_renamed_account_folder_gets_its_name_back(drive):
    _login(_phone("Phone 01"), "instagram", "ann.ig")
    storage_jobs.sync()
    account = _account(drive, "ann.ig")
    drive.rename_by_hand(account, "ANN (do not touch)")

    report = storage_jobs.sync()

    assert report.renamed == ["ann.ig - Anna"]
    assert report.created == []
    assert drive.named("ann.ig - Anna", ROOT) == [account]


def test_a_trashed_posted_folder_is_made_again_in_the_same_sweep(drive):
    _login(_phone("Phone 01"), "instagram", "ann.ig")
    storage_jobs.sync()
    old = _posted(drive, "ann.ig")
    drive.trash(old)

    report = storage_jobs.sync()

    new = _stored().posted_folder_id
    assert new != old
    assert drive.named(POSTED, _account(drive, "ann.ig")) == [new]
    assert report.posted_folders == ["ann.ig - Anna/POSTED"]
    assert drive.item(old).trashed


def test_a_deleted_posted_folder_is_made_again(drive):
    _login(_phone("Phone 01"), "instagram", "ann.ig")
    storage_jobs.sync()
    drive.delete(_posted(drive, "ann.ig"))

    storage_jobs.sync()

    assert drive.named(POSTED, _account(drive, "ann.ig")) == [
        _stored().posted_folder_id
    ]


def test_a_hand_renamed_posted_folder_gets_its_name_back(drive):
    _login(_phone("Phone 01"), "instagram", "ann.ig")
    storage_jobs.sync()
    posted = _posted(drive, "ann.ig")
    drive.rename_by_hand(posted, "done")

    report = storage_jobs.sync()

    assert drive.item(posted).name == POSTED
    assert _stored().posted_folder_id == posted
    assert report.renamed == ["ann.ig - Anna/POSTED"]


def test_a_posted_folder_dragged_out_is_left_there_and_a_new_one_made(drive):
    _login(_phone("Phone 01"), "instagram", "ann.ig")
    storage_jobs.sync()
    posted = _posted(drive, "ann.ig")
    drive.move(posted, ROOT)

    storage_jobs.sync()

    new = _stored().posted_folder_id
    assert new != posted
    assert drive.named(POSTED, _account(drive, "ann.ig")) == [new]
    assert drive.item(posted).parents == [ROOT]


@pytest.mark.parametrize(
    "remove", [FakeDrive.trash, FakeDrive.delete], ids=["trash", "delete"]
)
def test_an_account_folder_deleted_in_drive_removes_the_account_everywhere(
    drive, remove
):
    phone = _phone("Phone 01")
    added = devices.add_login(
        phone, LoginFields(service="instagram", username="ann.ig", password="pw")
    )
    assert added is not None
    login = added.id
    track("instagram", "ann.ig", user_id=users.agency_id())
    storage_jobs.sync()
    account = _account(drive, "ann.ig")
    remove(drive, account)

    report = storage_jobs.sync()

    assert report.created == []
    assert drive.named("ann.ig - Anna", ROOT) == []
    assert storage_jobs.sync().created == []
    assert [x.username for d in devices.list_devices() for x in d.logins] == []
    [archived] = devices.archived_logins()
    assert (archived.id, archived.username, archived.on) == (
        login,
        "ann.ig",
        ["Phone 01"],
    )
    assert devices.secrets(login).password == "pw"
    assert "ann.ig" not in [a.handle for a in list_accounts()]

    client = _client()
    assert "Archived accounts (1)" in client.get("/phones").text
    assert client.post(f"/phones/logins/{login}/restore").status_code == 200
    assert [x.username for d in devices.list_devices() for x in d.logins] == ["ann.ig"]
    assert "ann.ig" in [a.handle for a in list_accounts()]
    assert storage_jobs.sync().created == ["ann.ig - Anna"]


def test_state_before_posted_folders_heals_on_the_first_sweep(drive):
    login = _login(_phone("Phone 01"), "instagram", "ann.ig")
    account = drive.seed_folder("ann.ig - Anna", ROOT, owner=AGENCY)
    day = drive.seed_folder("2026-10-21", account, owner=AGENCY)
    upload = drive.add_file(day, "reel.mp4", "video/mp4")
    store.save_account_folder(login, account, "ann.ig - Anna", ROOT)
    assert store.account_folders().get(login).posted_folder_id is None

    report = storage_jobs.sync()

    assert report.created == report.adopted == []
    assert report.posted_folders == ["ann.ig - Anna/POSTED"]
    assert drive.writes == ["create"]
    assert drive.item(upload).parents == [day]
    assert drive.item(day).name == "2026-10-21"


def test_a_new_account_folder_drops_the_old_posted_id(drive):
    login = _login(_phone("Phone 01"), "instagram", "ann.ig")
    store.save_account_folder(login, "folder-a", "ann.ig - Anna", ROOT)
    store.save_posted_folder(login, "posted-a")
    store.save_account_folder(login, "folder-a", "ann.ig - Kate", ROOT)
    assert store.account_folders().get(login).posted_folder_id == "posted-a"

    store.save_account_folder(login, "folder-b", "ann.ig - Anna", ROOT)

    assert store.account_folders().get(login).posted_folder_id is None


def test_one_failing_login_does_not_stop_the_others(drive):
    _login(_phone("Phone 01"), "instagram", "ann.ig")
    _login(_phone("Phone 02", owner="Bob"), "instagram", "bob.ig")
    drive.fail_on(["ann.ig - Anna"])

    report = storage_jobs.sync()

    assert len(report.failed) == 1
    assert "ann.ig - Anna" in report.failed[0]
    assert _posted(drive, "bob.ig", owner="Bob")
    drive.fail_on([])
    assert storage_jobs.sync().created == ["ann.ig - Anna"]


def test_a_login_deleted_while_its_folder_is_made_is_skipped_and_nothing_is_deleted(
    drive, monkeypatch
):
    gone_phone = _phone("Phone 01")
    gone = _login(gone_phone, "instagram", "ann.ig")
    _login(_phone("Phone 02", owner="Bob"), "instagram", "bob.ig")
    real_create = drive.create_folder

    def create_then_lose_the_login(parent: str, name: str, **who):
        made = real_create(parent, name, **who)
        if name == "ann.ig - Anna":
            devices.remove_login(gone_phone, gone)
        return made

    monkeypatch.setattr(drive, "create_folder", create_then_lose_the_login)

    report = storage_jobs.sync()

    assert report.failed == []
    assert report.created == ["bob.ig - Bob"]
    assert _posted(drive, "bob.ig", owner="Bob")
    assert [t.handle for t in store.targets()] == ["bob.ig"]
    (kept,) = drive.named("ann.ig - Anna", ROOT)
    _login(_phone("Phone 03"), "instagram", "ann.ig")
    assert storage_jobs.sync().adopted == ["ann.ig - Anna"]
    assert _stored("ann.ig").folder_id == kept


def test_a_folder_saved_for_a_login_or_va_that_is_gone_saves_nothing(drive):
    assert store.save_account_folder(987_654_321, "f-1", "x - y", ROOT) is False
    assert store.account_folders().folders == []
    va = VaFolder(
        user_id=987_654_321, folder_id="f-2", name="Gone", root_folder_id=ROOT
    )
    assert store.save_va_folder(va) is False


def test_a_login_on_two_phones_gets_one_folder(drive):
    first = _phone("Phone 01", owner="Anna")
    second = _phone("Phone 02", owner="Bob")
    _login(first, "instagram", "ann.ig")
    _login(second, "instagram", "ann.ig")

    assert storage_jobs.sync().created == ["ann.ig - Anna"]


def test_deleting_the_login_forgets_its_folders_and_history(drive):
    phone = _phone("Phone 01")
    login = _login(phone, "instagram", "ann.ig")
    storage_jobs.sync()
    drive.add_file(_posted(drive, "ann.ig"), "reel.mp4", "video/mp4")
    storage_jobs.sync()
    devices.remove_login(phone, login)
    assert store.account_folders().folders == []
    assert drive.named("ann.ig - Anna", ROOT)


def test_a_video_moved_into_posted_counts_once(drive):
    _login(_phone("Phone 01"), "instagram", "ann.ig")
    storage_jobs.sync()
    reel = drive.add_file(_account(drive, "ann.ig"), "reel.mp4", "video/mp4")

    assert storage_jobs.sync().newly_posted == 0
    drive.move(reel, _posted(drive, "ann.ig"))
    assert storage_jobs.sync().newly_posted == 1
    assert storage_jobs.sync().newly_posted == 0

    card = _board().cards[0]
    assert card.posted_total == 1
    assert card.waiting == []
    assert card.uploaded_today.total == 1


def test_a_file_moved_back_out_waits_again_and_counts_once(drive):
    _login(_phone("Phone 01"), "instagram", "ann.ig")
    storage_jobs.sync()
    reel = drive.add_file(_posted(drive, "ann.ig"), "reel.mp4", "video/mp4")
    storage_jobs.sync()
    drive.move(reel, _account(drive, "ann.ig"))

    storage_jobs.sync()

    card = _board().cards[0]
    assert card.posted_total == 0
    assert card.uploaded_today.total == 1
    assert [f.id for f in card.waiting] == [reel]


def test_a_new_account_folder_starts_its_counts_at_zero(drive):
    login = _login(_phone("Phone 01"), "instagram", "ann.ig")
    storage_jobs.sync()
    drive.add_file(_posted(drive, "ann.ig"), "reel.mp4", "video/mp4")
    storage_jobs.sync()
    assert _board().cards[0].uploaded_today.total == 1

    store.forget_account_folder(login)
    drive.rename_by_hand(drive.named("ann.ig - Anna", ROOT)[0], "old ann")
    assert storage_jobs.sync().created == ["ann.ig - Anna"]

    card = _board().cards[0]
    assert (card.posted_total, card.uploaded_today.total) == (0, 0)


def test_uploaded_today_tells_clone_uploads_from_files_put_there_by_hand(drive):
    login = _login(_phone("Phone 01"), "instagram", "ann.ig")
    _login(_phone("Phone 02", owner="Bob"), "instagram", "bob.ig")
    storage_jobs.sync()
    account = _account(drive, "ann.ig")
    ours = drive.add_file(account, "clone.mp4", "video/mp4")
    drive.add_file(account, "hand.mp4", "video/mp4")
    moved = drive.add_file(account, "posted.mp4", "video/mp4")
    drive.move(moved, _posted(drive, "ann.ig"))
    before = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=2)
    drive.add_file(account, "old.mp4", "video/mp4", created=before)
    upload = upload_store.queue("job1", 0, login, users.agency_id(), "ann.ig - Anna")
    assert upload is not None
    upload_store.finish(upload, drive_file_id=ours, drive_url="https://drive/x")
    storage_jobs.sync()

    cards = {c.target.handle: c.uploaded_today for c in _board().cards}

    ann, bob = cards["ann.ig"], cards["bob.ig"]
    assert (ann.clone, ann.manual, ann.total) == (1, 2, 3)
    assert (bob.clone, bob.manual, bob.total) == (0, 0, 0)
    saved = (
        drive_client.storage_of(users.agency_id(), interactive=True)
        .folders.saved_board()
        .cards
    )
    assert {c.target.handle: c.uploaded_today for c in saved} == cards


def test_a_second_posted_folder_counts_and_is_flagged(drive):
    _login(_phone("Phone 01"), "instagram", "ann.ig")
    storage_jobs.sync()
    extra = drive.seed_folder("posted", _account(drive, "ann.ig"))
    drive.add_file(extra, "a.mp4", "video/mp4")
    drive.add_file(_posted(drive, "ann.ig"), "b.mp4", "video/mp4")

    assert storage_jobs.sync().newly_posted == 2

    card = _board().cards[0]
    assert card.posted_total == 2
    assert [f.id for f in card.scan.extra_posted_folders] == [extra]
    html = _client().get("/drive/live").text
    assert '1 more "POSTED" folder in here' in html
    assert drive.item(extra).name == "posted"


def test_uploaded_today_turns_at_local_midnight_in_the_drive_timezone(drive):
    _login(_phone("Phone 01"), "instagram", "ann.ig")
    storage_jobs.sync()
    posted = _posted(drive, "ann.ig")
    tz = ZoneInfo("Europe/Warsaw")
    for at, name in (
        (datetime.datetime(2026, 10, 21, 21, 0, tzinfo=tz), "y1.mp4"),
        (datetime.datetime(2026, 10, 21, 23, 30, tzinfo=tz), "y2.mp4"),
        (datetime.datetime(2026, 10, 22, 9, 0, tzinfo=tz), "today.mp4"),
    ):
        with time_machine.travel(at, tick=False):
            drive.add_file(posted, name, "video/mp4")
            storage_jobs.sync()
            card = _board().cards[0]

    assert (card.uploaded_today.manual, card.posted_total) == (1, 3)


@pytest.mark.parametrize(
    ("at", "start"),
    [
        ("2026-07-01T20:00:00+00:00", "2026-07-01T18:45:00+00:00"),
        ("2026-12-01T20:00:00+00:00", "2026-12-01T18:45:00+00:00"),
        ("2026-12-02T00:30:00+00:00", "2026-12-01T18:45:00+00:00"),
        ("2026-12-02T10:00:00+00:00", "2026-12-01T18:45:00+00:00"),
        ("2026-12-02T18:45:00+00:00", "2026-12-02T18:45:00+00:00"),
    ],
)
def test_a_posting_day_starts_at_18_45_utc_all_year(at, start):
    found = posting.posting_day_start(datetime.datetime.fromisoformat(at))
    assert found == datetime.datetime.fromisoformat(start)


def test_the_posting_time_is_a_setting(monkeypatch):
    monkeypatch.setenv("OFM_GDRIVE_POSTING_TIME_UTC", "16:00")
    monkeypatch.setenv("OFM_GDRIVE_POSTING_BEFORE_H", "2")
    at = datetime.datetime(2026, 12, 1, 15, 0, tzinfo=datetime.UTC)
    assert posting.posting_day_start(at).hour == 14


def _utc(text: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(text)


@pytest.mark.parametrize(
    ("at", "start", "end", "is_open"),
    [
        (
            "2026-12-01T18:44:00+00:00",
            "2026-12-01T18:45:00+00:00",
            "2026-12-02T00:45:00+00:00",
            False,
        ),
        (
            "2026-12-01T18:45:00+00:00",
            "2026-12-01T18:45:00+00:00",
            "2026-12-02T00:45:00+00:00",
            True,
        ),
        (
            "2026-12-02T00:44:00+00:00",
            "2026-12-01T18:45:00+00:00",
            "2026-12-02T00:45:00+00:00",
            True,
        ),
        (
            "2026-12-02T00:45:00+00:00",
            "2026-12-02T18:45:00+00:00",
            "2026-12-03T00:45:00+00:00",
            False,
        ),
        (
            "2026-12-02T10:00:00+00:00",
            "2026-12-02T18:45:00+00:00",
            "2026-12-03T00:45:00+00:00",
            False,
        ),
    ],
)
def test_the_posting_window_is_open_18_45_to_00_45_utc(at, start, end, is_open):
    found = posting.window(_utc(at))
    assert found.start == _utc(start).timestamp()
    assert found.end == _utc(end).timestamp()
    assert found.open is is_open


def test_the_posting_window_follows_its_settings(monkeypatch):
    monkeypatch.setenv("OFM_GDRIVE_POSTING_TIME_UTC", "12:00")
    monkeypatch.setenv("OFM_GDRIVE_POSTING_BEFORE_H", "0.5")
    monkeypatch.setenv("OFM_GDRIVE_POSTING_AFTER_H", "2")
    found = posting.window(_utc("2026-12-01T11:30:00+00:00"))
    assert found.open is True
    assert found.end == _utc("2026-12-01T14:00:00+00:00").timestamp()


def test_every_page_carries_the_posting_window_hidden(fake_externals):
    client = TestClient(app)
    with time_machine.travel(_utc("2026-12-01T20:00:00+00:00"), tick=False):
        sign_in_agency(client)
        page = client.get("/home").text
    start = int(_utc("2026-12-01T18:45:00+00:00").timestamp())
    end = int(_utc("2026-12-02T00:45:00+00:00").timestamp())
    assert f'data-start="{start}" data-end="{end}" hidden' in page
    assert "Start posting content" in page


def test_one_account_failing_to_list_does_not_stop_the_tally(drive, monkeypatch):
    _login(_phone("Phone 01"), "instagram", "ann.ig")
    _login(_phone("Phone 02", owner="Bob"), "instagram", "bob.ig")
    storage_jobs.sync()
    ann_posted = _posted(drive, "ann.ig")
    drive.add_file(_posted(drive, "bob.ig", owner="Bob"), "b.mp4", "video/mp4")
    real_children = drive.children

    def children(parent_id, *, folders_only=False):
        if parent_id == ann_posted:
            msg = "Drive refused"
            raise DriveError(msg)
        return real_children(parent_id, folders_only=folders_only)

    monkeypatch.setattr(drive, "children", children)

    report = storage_jobs.sync()

    assert report.newly_posted == 1
    assert len(report.failed) == 1
    assert "ann.ig - Anna" in report.failed[0]


def _three_logins() -> None:
    for i, owner in enumerate(("Anna", "Bob", "Cleo")):
        _login(_phone(f"Phone 0{i}", owner=owner), "instagram", f"h{i}.ig")


def _assert_one_of_each(drive: FakeDrive) -> None:
    for i, owner in enumerate(("Anna", "Bob", "Cleo")):
        (account,) = drive.named(f"h{i}.ig - {owner}", ROOT)
        assert len(drive.named(POSTED, account)) == 1


def test_a_sync_during_another_does_nothing(drive, monkeypatch):
    _three_logins()
    inside = threading.Event()
    release = threading.Event()
    real_create = drive.create_folder

    def slow_create(parent: str, name: str, **who):
        inside.set()
        assert release.wait(10)
        return real_create(parent, name, **who)

    monkeypatch.setattr(drive, "create_folder", slow_create)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(storage_jobs.sync)
        assert inside.wait(10)
        second = pool.submit(storage_jobs.sync).result(timeout=10)
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
        return storage_jobs.sync()

    with ThreadPoolExecutor(max_workers=4) as pool:
        reports = [f.result(timeout=30) for f in [pool.submit(racer) for _ in range(4)]]

    assert sum(len(r.created) for r in reports) == 3
    assert sum(len(r.posted_folders) for r in reports) == 3
    _assert_one_of_each(drive)


def test_page_shows_what_waits_and_what_was_posted(drive):
    ann = _login(_phone("Phone 01"), "instagram", "ann.ig")
    _login(_phone("Phone 02", owner="Bob"), "instagram", "bob.ig")
    storage_jobs.sync()
    account = _account(drive, "ann.ig")
    video = drive.add_file(account, "reel1.mp4", "video/mp4", thumbnail=True)
    drive.add_file(account, "notes.txt", "text/plain")
    drive.add_file(_posted(drive, "ann.ig"), "old.mp4", "video/mp4")
    drive.seed_folder("old messy folder", ROOT)
    storage_jobs.sync()

    html = _client().get("/drive/live").text

    assert "waiting to post in" not in html
    assert "Posted today" not in html
    assert "Uploaded to Drive today: <strong>3</strong>" in html
    assert "Waiting: <strong>2</strong>" in html
    assert "Clone:" not in html
    assert "Manual:" not in html
    assert f'src="/drive/thumb/{ann}/{video}"' in html
    assert "notes.txt" in html
    assert "Nothing waiting to post" in html
    assert "old messy folder" in html
    assert "ann.ig - Anna" not in html.split("other folder")[-1]


def test_other_folders_in_an_account_folder_are_ignored(drive):
    _login(_phone("Phone 01"), "instagram", "ann.ig")
    storage_jobs.sync()
    day = drive.seed_folder("2026-10-21", _account(drive, "ann.ig"))
    drive.add_file(day, "a.mp4", "video/mp4")
    drive.clear_calls()

    html = _client().get("/drive/live").text

    assert "2026-10-21" not in html
    assert "Nothing waiting to post" in html
    assert drive.writes == []
    assert drive.calls.count("children") == 1
    assert drive.calls.count("children_of") == 2


def test_page_lists_every_account_in_a_fixed_number_of_calls(drive):
    for n in range(6):
        _login(_phone(f"Phone {n:02d}"), "instagram", f"user{n}.ig")
    storage_jobs.sync()
    for n in range(6):
        drive.add_file(_account(drive, f"user{n}.ig"), f"v{n}.mp4", "video/mp4")
    drive.clear_calls()

    html = _client().get("/drive/live").text

    assert html.count('class="dr-file"') == 6
    assert sorted(drive.calls) == ["children", "children_of", "children_of", "folder"]


def test_a_trashed_account_folder_is_still_told_on_the_page(drive):
    _login(_phone("Phone 01"), "instagram", "ann.ig")
    _login(_phone("Phone 02", owner="Bea"), "instagram", "bea.ig")
    storage_jobs.sync()
    drive.trash(_account(drive, "ann.ig"))

    html = _client().get("/drive/live").text

    assert "The account&#39;s folder was deleted" in html
    assert "Nothing waiting to post" in html


def test_page_sees_a_trashed_account_folder_whose_contents_went_with_it(drive):
    _login(_phone("Phone 01"), "instagram", "ann.ig")
    storage_jobs.sync()
    account = _account(drive, "ann.ig")
    drive.add_file(account, "kept.jpg")
    drive.trash(account)
    with pytest.raises(FolderGoneError):
        drive.folder(account)

    html = _client().get("/drive/live").text

    assert "The account&#39;s folder was deleted" in html
    assert store.account_folders().folders == []
    assert storage_jobs.sync().created == []


def test_drive_is_in_the_nav():
    assert 'href="/drive"' in _client().get("/phones").text


def test_page_needs_login():
    response = TestClient(app).get("/drive", follow_redirects=False)
    assert response.status_code == 303


def test_a_card_that_fails_to_load_says_so(drive, monkeypatch):
    _login(_phone("Phone 01"), "instagram", "ann.ig")
    storage_jobs.sync()
    real_children = drive.children

    def children(parent_id, *, folders_only=False):
        if not folders_only:
            msg = "boom"
            raise RuntimeError(msg)
        return real_children(parent_id, folders_only=folders_only)

    monkeypatch.setattr(drive, "children", children)

    html = _client().get("/drive/live").text

    assert "Could not read this folder from Drive" in html
    assert "Nothing waiting to post" not in html


def test_sync_route_reports(drive):
    _login(_phone("Phone 01"), "instagram", "ann.ig")
    response = _client().post("/drive/sync")
    assert response.status_code == 200
    body = response.json()
    assert body["created"] == ["ann.ig - Anna"]
    assert body["posted_folders"] == ["ann.ig - Anna/POSTED"]
    assert body["newly_posted"] == 0


def _uploaded_preview(drive: FakeDrive) -> str:
    login = _login(_phone("Phone 01"), "instagram", "ann.ig")
    storage_jobs.sync()
    file_id = drive.add_file(_account(drive, "ann.ig"), "a.jpg", thumbnail=True)
    return f"{login}/{file_id}"


def test_thumb_route_serves_an_account_folder_file_and_caches_privately(drive):
    file_id = _uploaded_preview(drive)
    response = _client().get(f"/drive/thumb/{file_id}")
    assert response.status_code == 200
    assert response.content.startswith(b"\xff\xd8")
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["cache-control"] == "private, max-age=86400"


def test_thumb_is_fetched_from_drive_once(drive):
    file_id = _uploaded_preview(drive)
    first = _client().get(f"/drive/thumb/{file_id}")
    drive.clear_calls()

    again = _client().get(f"/drive/thumb/{file_id}")

    assert again.status_code == 200
    assert again.content == first.content
    assert again.headers["content-type"] == "image/jpeg"
    assert drive.calls == ["file"]


def test_thumb_route_serves_a_posted_file(drive):
    login = _uploaded_preview(drive).split("/")[0]
    posted = drive.add_file(_posted(drive, "ann.ig"), "p.jpg", thumbnail=True)
    assert _client().get(f"/drive/thumb/{login}/{posted}").status_code == 200


def test_thumb_route_without_preview_is_404(drive):
    login = _login(_phone("Phone 01"), "instagram", "ann.ig")
    storage_jobs.sync()
    file_id = drive.add_file(_account(drive, "ann.ig"), "a.txt", "text/plain")
    assert _client().get(f"/drive/thumb/{login}/{file_id}").status_code == 404


def test_thumb_route_refuses_a_random_id(drive):
    login = _uploaded_preview(drive).split("/")[0]
    response = _client().get(f"/drive/thumb/{login}/RandomFileId0123456789")
    assert response.status_code == 404
    assert "thumbnail" not in drive.calls


def test_thumb_route_refuses_a_file_outside_the_apps_folders(drive):
    login = _uploaded_preview(drive).split("/")[0]
    loose = drive.add_file(ROOT, "passport.jpg", thumbnail=True)
    elsewhere = drive.seed_folder("Someone else's", shared=True)
    private = drive.add_file(elsewhere, "private.jpg", thumbnail=True)

    for file_id in (loose, private):
        assert _client().get(f"/drive/thumb/{login}/{file_id}").status_code == 404
    assert "thumbnail" not in drive.calls


def test_thumb_route_refuses_a_trashed_file(drive):
    preview = _uploaded_preview(drive)
    drive.trash(preview.split("/")[1])
    assert _client().get(f"/drive/thumb/{preview}").status_code == 404


def test_thumb_route_rejects_a_bad_id(drive):
    assert _client().get("/drive/thumb/1/..%2Fetc").status_code in (404, 422)
    assert drive.calls == []


def test_thumb_route_needs_login(drive):
    file_id = _uploaded_preview(drive)
    response = TestClient(app).get(f"/drive/thumb/{file_id}", follow_redirects=False)
    assert response.status_code == 303


def test_the_agencys_hand_made_root_is_adopted_in_any_case(
    fake_externals, tmp_path, monkeypatch
):
    monkeypatch.setattr(
        drive_folders, "storage_constants", lambda: StorageConstants(dev_prefix="")
    )
    monkeypatch.setenv("OFM_UPLOADS_ROOT", str(tmp_path))
    fake = fake_drive()
    fake.seed_folder("My Drive", item_id=MY_DRIVE, owner=AGENCY, shared=True)
    root = fake.seed_folder("all accounts", MY_DRIVE, owner=AGENCY)
    existing = fake.seed_folder("ann.ig - Anna", root)
    users._repository().signed_in(users.agency_id(), "", f"fake-refresh:{AGENCY}")
    _login(_phone("Phone 01"), "instagram", "ann.ig")

    report = storage_jobs.sync()

    assert users.get_user(users.agency_id()).drive_root_id == root
    assert report.adopted == ["ann.ig - Anna"]
    assert fake.named("ann.ig - Anna", root) == [existing]
    assert fake.named("ALL ACCOUNTS", MY_DRIVE) == []


def test_dev_never_adopts_the_live_root_it_makes_its_own(
    fake_externals, tmp_path, monkeypatch
):
    monkeypatch.setenv("OFM_UPLOADS_ROOT", str(tmp_path))
    fake = fake_drive()
    fake.seed_folder("My Drive", item_id=MY_DRIVE, owner=AGENCY, shared=True)
    live = fake.seed_folder("ALL ACCOUNTS", MY_DRIVE, owner=AGENCY)
    fake.seed_folder("ann.ig - Anna", live)
    users._repository().signed_in(users.agency_id(), "", f"fake-refresh:{AGENCY}")
    _login(_phone("Phone 01"), "instagram", "ann.ig")

    storage_jobs.sync()

    (dev_root,) = fake.named("DEV - ALL ACCOUNTS", MY_DRIVE)
    assert users.get_user(users.agency_id()).drive_root_id == dev_root
    assert fake.named("ann.ig - Anna", dev_root)
    assert len(fake.named("ann.ig - Anna", live)) == 1


def test_a_root_trashed_by_hand_is_found_or_made_again(drive):
    _login(_phone("Phone 01"), "instagram", "ann.ig")
    storage_jobs.sync()
    drive.trash(ROOT)

    storage_jobs.sync()

    (again,) = drive.named("DEV - ALL ACCOUNTS", MY_DRIVE)
    assert again != ROOT
    assert drive.named("ann.ig - Anna", again)


def test_an_owner_without_google_drive_gets_nothing_made_and_is_told(drive):
    vee = make_user("vee@example.com")
    phone = devices.add_device(
        DeviceFields(
            label="Vee 01", model_id="iphone-13", color_id="blue", owner="Vee"
        ),
        user_id=vee.id,
    )
    _login(phone, "instagram", "vee.ig")
    drive.clear_calls()

    report = storage_jobs.sync()

    assert report.not_connected == ["vee@example.com"]
    assert drive.writes == []
    vee_client = TestClient(app)
    sign_in(vee_client, "vee@example.com")
    html = vee_client.get("/drive/live").text
    assert "vee@example.com has not connected Google Drive" in html


def test_the_folder_owners_files_are_theirs(drive):
    _login(_phone("Phone 01"), "instagram", "ann.ig")
    storage_jobs.sync()
    assert drive.item(_account(drive, "ann.ig")).owner == AGENCY
    assert drive.item(_posted(drive, "ann.ig")).owner == AGENCY


def test_cards_are_ordered_by_the_accounts_followers(drive):
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
    html = _client().get("/drive/live").text
    order = [html.index(f">@{h}") for h in ("big.ig", "small.ig", "new.ig")]
    assert order == sorted(order)
    assert '@big.ig <span class="dr-followers">· 90k followers</span>' in html
    assert ">@new.ig</p>" in html


def test_the_owner_sits_next_to_the_phone_model(drive):
    _login(_phone("Phone 01", owner="Anna"), "instagram", "ann.ig")
    html = _client().get("/drive/live").text
    assert '<p class="ph-specs">iPhone 13 · Blue · Anna</p>' in html
    assert 'class="ph-meta"' not in html


def test_the_page_opens_with_no_drive_call_from_the_last_read(drive):
    _login(_phone("Phone 01"), "instagram", "ann.ig")
    storage_jobs.sync()
    drive.add_file(_account(drive, "ann.ig"), "new.mp4", "video/mp4")
    drive.clear_calls()

    html = _client().get("/drive").text

    assert drive.calls == []
    assert 'data-live="/drive/live"' in html
    assert 'class="dr-asof"' in html
    assert "Nothing waiting to post" in html

    live = _client().get("/drive/live").text
    assert "new.mp4" in live
    assert 'class="dr-asof"' not in live
    drive.clear_calls()
    assert "new.mp4" in _client().get("/drive").text
    assert drive.calls == []


def test_a_folder_never_read_says_so_until_the_live_read(drive):
    login = _login(_phone("Phone 01"), "instagram", "ann.ig")
    folder = drive.create_folder(ROOT, "ann.ig - Anna")
    store.save_account_folder(login, folder.id, "ann.ig - Anna", ROOT)

    html = _client().get("/drive").text
    assert "Reading from Drive" in html
    assert "Reading from Drive" not in _client().get("/drive/live").text


def _user_phone(email: str, handle: str, owner: str, *, google: bool = True) -> int:
    user = make_user(email)
    if google:
        users._repository().signed_in(user.id, "", f"fake-refresh:{email}")
    phone = devices.add_device(
        DeviceFields(
            label=f"{owner} 01", model_id="iphone-13", color_id="blue", owner=owner
        ),
        user_id=user.id,
    )
    return _login(phone, "instagram", handle)


def test_a_users_sync_does_and_reports_only_their_own_folders(drive):
    _user_phone("ann@example.com", "ann.ig", "Ann")
    _user_phone("bob@example.com", "bob.ig", "Bob")
    _user_phone("cy@example.com", "cy.ig", "Cy", google=False)
    ann = TestClient(app)
    sign_in(ann, "ann@example.com")

    body = ann.post("/drive/sync").json()

    assert body["created"] == ["ann.ig - Ann"]
    assert body["posted_folders"] == ["ann.ig - Ann/POSTED"]
    assert body["not_connected"] == []
    assert body["failed"] == []
    assert users.by_email("bob@example.com").drive_root_id is None

    admin = _client().post("/drive/sync").json()
    assert admin["created"] == []
    assert admin["not_connected"] == []
    assert users.by_email("bob@example.com").drive_root_id is None

    sweep = storage_jobs.sync()
    assert sweep.created == ["bob.ig - Bob"]
    assert sweep.not_connected == ["cy@example.com"]


def test_dev_never_uses_a_stored_live_root_from_a_prod_copy(drive):
    login = _login(_phone("Phone 01"), "instagram", "ann.ig")
    live = drive.seed_folder("ALL ACCOUNTS", MY_DRIVE, owner=AGENCY)
    live_account = drive.seed_folder("ann.ig - hand named", live, owner=AGENCY)
    live_posted = drive.seed_folder(POSTED, live_account, owner=AGENCY)
    store.save_account_folder(login, live_account, "ann.ig - Anna", live)
    store.save_posted_folder(login, live_posted)
    users.set_drive_root(users.agency_id(), live)

    report = storage_jobs.sync()

    assert users.get_user(users.agency_id()).drive_root_id == ROOT
    assert report.created == ["ann.ig - Anna"]
    assert _stored().folder_id == _account(drive, "ann.ig")
    assert drive.item(live).name == "ALL ACCOUNTS"
    assert drive.names_in(live) == ["ann.ig - hand named"]
    assert drive.names_in(live_account) == [POSTED]


def test_the_page_forgets_a_stored_live_root_in_dev(drive):
    login = _login(_phone("Phone 01"), "instagram", "ann.ig")
    live = drive.seed_folder("ALL ACCOUNTS", MY_DRIVE, owner=AGENCY)
    live_account = drive.seed_folder("ann.ig - Anna", live, owner=AGENCY)
    drive.add_file(live_account, "live.mp4", "video/mp4")
    store.save_account_folder(login, live_account, "ann.ig - Anna", live)
    users.set_drive_root(users.agency_id(), live)

    html = _client().get("/drive/live").text

    assert "live.mp4" not in html
    assert users.get_user(users.agency_id()).drive_root_id is None
    assert store.account_folders().folders == []


def test_a_404_in_the_batch_keeps_the_root_and_lists_one_by_one(drive, monkeypatch):
    _login(_phone("Phone 01"), "instagram", "ann.ig")
    storage_jobs.sync()
    drive.add_file(_account(drive, "ann.ig"), "new.mp4", "video/mp4")

    def gone(_parent_ids):
        msg = "Folder x: not found"
        raise FolderGoneError(msg)

    monkeypatch.setattr(drive, "children_of", gone)

    html = _client().get("/drive/live").text

    assert users.get_user(users.agency_id()).drive_root_id == ROOT
    assert "new.mp4" in html


BOT_ROOT = "configured-all-accounts"


@pytest.fixture
def bot_drive(fake_externals, monkeypatch, tmp_path) -> FakeDrive:
    monkeypatch.setenv("OFM_UPLOADS_ROOT", str(tmp_path))
    monkeypatch.setenv("OFM_GDRIVE_SERVICE_ACCOUNT", "{}")
    monkeypatch.setenv("OFM_GDRIVE_ROOT_FOLDER", BOT_ROOT)
    fake = fake_drive()
    fake.rename_by_hand(BOT_ROOT, "DEV - ALL ACCOUNTS")
    return fake


def test_the_agency_admin_without_google_is_read_through_the_bot(bot_drive):
    assert users.refresh_token(users.agency_id()) == ""
    _login(_phone("Phone 01"), "instagram", "ann.ig")

    report = storage_jobs.sync()

    assert report.not_connected == []
    assert report.created == ["ann.ig - Anna"]
    assert bot_drive.named("ann.ig - Anna", BOT_ROOT)
    assert users.get_user(users.agency_id()).drive_root_id == BOT_ROOT
    html = _client().get("/drive/live").text
    assert "Connect it once" not in html
    assert "has not connected Google Drive" not in html
    assert "@ann.ig" in html


def test_the_bot_never_makes_a_root_of_its_own(bot_drive):
    bot_drive.rename_by_hand(BOT_ROOT, "ALL ACCOUNTS")
    _login(_phone("Phone 01"), "instagram", "ann.ig")

    report = storage_jobs.sync()

    assert report.created == []
    assert any("cannot use OFM_GDRIVE_ROOT_FOLDER" in f for f in report.failed)
    assert bot_drive.named("DEV - ALL ACCOUNTS", "root") == []


def test_only_the_agency_admin_falls_back_to_the_bot(bot_drive):
    vee = users.add_user(UserForm(email="vee@example.com"))
    phone = devices.add_device(
        DeviceFields(label="Vee 01", model_id="iphone-13", color_id="blue"),
        user_id=vee.id,
    )
    _login(phone, "instagram", "vee.ig")

    report = storage_jobs.sync(vee.id)

    assert report.not_connected == ["vee@example.com"]
    assert bot_drive.named("vee.ig - Vee 01", BOT_ROOT) == []


def test_an_unusable_bot_key_is_no_fallback(bot_drive, monkeypatch):
    monkeypatch.setattr(drive_client, "service_account_email", lambda: None)
    _login(_phone("Phone 01"), "instagram", "ann.ig")

    report = storage_jobs.sync()

    assert report.not_connected == [users.get_user(users.agency_id()).email]
    assert report.failed == []


def _tracked_login(phone: int, handle: str = "ann.ig") -> tuple[int, int]:
    added = devices.add_login(
        phone, LoginFields(service="instagram", username=handle, password="pw")
    )
    assert added is not None
    track("instagram", handle, user_id=users.agency_id())
    (account,) = [a.id for a in list_accounts() if a.handle == handle]
    return added.id, account


def test_removed_on_accounts_it_leaves_the_phones_and_drive_password_kept(drive):
    login, account = _tracked_login(_phone("Phone 01"))
    other_login, _ = _tracked_login(_phone("Phone 02", owner="Bea"), "bea.ig")
    storage_jobs.sync()
    folder = _account(drive, "ann.ig")

    r = _client().post("/accounts/delete", json={"ids": [account]})

    assert r.json()["count"] == 1
    assert drive.item(folder).trashed
    assert [x.id for d in devices.list_devices() for x in d.logins] == [other_login]
    assert [a.id for a in devices.archived_logins()] == [login]
    assert devices.secrets(login).password == "pw"
    assert "ann.ig" not in [a.handle for a in list_accounts()]
    assert storage_jobs.sync().created == []


def test_off_its_last_phone_it_leaves_accounts_and_drive(drive):
    phone = _phone("Phone 01")
    login, _ = _tracked_login(phone)
    storage_jobs.sync()
    folder = _account(drive, "ann.ig")

    r = _client().post(f"/phones/{phone}/logins/{login}/delete")

    assert r.status_code == 200
    assert drive.item(folder).trashed
    assert "ann.ig" not in [a.handle for a in list_accounts()]
    assert devices.archived_logins() == []


def test_off_one_of_two_phones_it_stays_everywhere(drive):
    one, two = _phone("Phone 01"), _phone("Phone 02")
    login, _ = _tracked_login(one)
    assert devices.add_login(two, LoginFields(service="instagram", username="ann.ig"))
    storage_jobs.sync()
    folder = _account(drive, "ann.ig")

    _client().post(f"/phones/{one}/logins/{login}/delete")

    assert not drive.item(folder).trashed
    assert "ann.ig" in [a.handle for a in list_accounts()]


def test_a_deleted_phone_takes_only_its_own_accounts_along(drive):
    one, two = _phone("Phone 01"), _phone("Phone 02", owner="Bea")
    _tracked_login(one)
    _tracked_login(two, "bea.ig")
    storage_jobs.sync()
    ann, bea = _account(drive, "ann.ig"), _account(drive, "bea.ig", owner="Bea")

    _client().post(f"/phones/{one}/delete")

    assert drive.item(ann).trashed
    assert not drive.item(bea).trashed
    assert [a.handle for a in list_accounts()] == ["bea.ig"]


def test_adding_an_archived_account_again_brings_it_back(drive):
    phone = _phone("Phone 01")
    login, account = _tracked_login(phone)
    storage_jobs.sync()
    _client().post("/accounts/delete", json={"ids": [account]})

    again = devices.add_login(
        phone, LoginFields(service="instagram", username="ann.ig")
    )

    assert again == AddedLogin(id=login, existed=True)
    assert devices.archived_logins() == []
    assert devices.secrets(login).password == "pw"


def test_a_posted_folder_gone_mid_scan_never_removes_the_account(drive, monkeypatch):
    _tracked_login(_phone("Phone 01"))
    storage_jobs.sync()
    posted = _posted(drive, "ann.ig")
    real_children = drive.children

    def children(parent_id, *, folders_only=False):
        if parent_id == posted:
            raise FolderGoneError(posted)
        return real_children(parent_id, folders_only=folders_only)

    monkeypatch.setattr(drive, "children", children)

    storage_jobs.sync()

    assert devices.archived_logins() == []
    assert "ann.ig" in [a.handle for a in list_accounts()]


def test_an_archived_account_with_no_phone_left_is_not_restored(drive):
    phone = _phone("Phone 01")
    login, account = _tracked_login(phone)
    _client().post("/accounts/delete", json={"ids": [account]})
    _client().post(f"/phones/{phone}/delete")

    r = _client().post(f"/phones/logins/{login}/restore")

    assert r.status_code == 409
    assert [a.id for a in devices.archived_logins()] == [login]
    assert devices.secrets(login).password == "pw"


def test_an_archived_account_is_restored_onto_its_phone(drive):
    phone = _phone("Phone 01")
    login, account = _tracked_login(phone)
    _client().post("/accounts/delete", json={"ids": [account]})

    r = _client().post(f"/phones/logins/{login}/restore")

    assert r.status_code == 200
    assert devices.archived_logins() == []
    assert [x.id for d in devices.list_devices() for x in d.logins] == [login]
    assert "ann.ig" in [a.handle for a in list_accounts()]


def test_a_live_read_of_many_accounts_writes_each_table_once(drive):
    _three_logins()
    storage_jobs.sync()
    for i, owner in enumerate(("Anna", "Bob", "Cleo")):
        drive.add_file(_posted(drive, f"h{i}.ig", owner), f"old{i}.mp4", "video/mp4")

    with counting() as seen:
        assert _client().get("/drive/live").status_code == 200

    assert len(seen.writing("drive_posted_files")) == 1
    assert len(seen.writing("drive_account_folders")) == 1
    folders = store.account_folders().folders
    assert len(folders) == 3
    assert [len(f.scan.posted) for f in folders if f.scan] == [1, 1, 1]


def test_a_login_gone_during_a_live_read_loses_only_its_own_posted_files(
    drive, monkeypatch, caplog
):
    _three_logins()
    storage_jobs.sync()
    for i, owner in enumerate(("Anna", "Bob", "Cleo")):
        drive.add_file(_posted(drive, f"h{i}.ig", owner), f"old{i}.mp4", "video/mp4")
    [gone] = [t for t in store.targets() if t.handle == "h0.ig"]
    listed = drive.children_of
    calls: list[list[str]] = []

    def listed_while_a_phone_goes(parent_ids: list[str]):
        calls.append(parent_ids)
        if len(calls) == 2:
            devices.delete_device(gone.device.id)
        return listed(parent_ids)

    monkeypatch.setattr(drive, "children_of", listed_while_a_phone_goes)

    with caplog.at_level("INFO", logger="ofmhelpers.features.storage.folders"):
        assert _client().get("/drive/live").status_code == 200

    assert (
        f"login {gone.login_id} went meanwhile, its 1 posted files are not kept"
        in caplog.text
    )
    with session_scope() as s:
        saved = s.execute(text("SELECT name FROM drive_posted_files")).scalars()
        assert sorted(saved) == ["old1.mp4", "old2.mp4"]
    scans = {f.login_id: f.scan for f in store.account_folders().folders}
    assert gone.login_id not in scans
    assert [len(scan.posted) for scan in scans.values() if scan] == [1, 1]
