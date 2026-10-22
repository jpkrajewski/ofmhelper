"""/drive: every Instagram login on an active phone gets "<handle> - <owner>"
in the shared Drive folder, which the VA uploads into, and a POSTED folder
inside it, which people move a video into once posted. Every sweep heals
whatever it finds: a re-run writes nothing, hand-made folders are reused, a
new owner renames, deleted / trashed / renamed / dragged-out folders come
back, concurrent sweeps never duplicate, the live folder is refused outside
production, nothing in Drive is ever moved or deleted. The page shows what
waits in each folder (previews proxied through the app, and only those) and
how much was posted: now, today, in 7 days."""

import datetime
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ.setdefault("SESSION_SECRET", "test-secret")

import pytest
import time_machine
from fastapi.testclient import TestClient
from signin import sign_in_agency

from ofmhelpers.gdrive import FolderGoneError
from ofmhelpers.gdrive.fake import FakeDrive, fake_drive
from ofmhelpers.web import drive_folders
from ofmhelpers.web.main import app
from ofmhelpers.web.schemas import DeviceFields, LoginFields
from ofmhelpers.web.stores import devices, users
from ofmhelpers.web.stores import drive_folders as store

# The agency's Google account (the password admin's, who owns the phones
# `_phone` makes): its My Drive, and the "DEV - ALL ACCOUNTS" in it.
AGENCY = "agency@example.com"
MY_DRIVE = f"mydrive-{AGENCY}"
ROOT = "agency-root-folder"
POSTED = "POSTED"


@pytest.fixture
def drive(fake_externals, monkeypatch, tmp_path) -> FakeDrive:
    """The fake Drive, the agency's Google Drive connected, its
    "DEV - ALL ACCOUNTS" folder already there (adopted by the first sweep)."""
    # Fake ids repeat from test to test; the kept previews must not.
    monkeypatch.setenv("OFM_UPLOADS_ROOT", str(tmp_path))
    fake = fake_drive()
    fake.seed_folder("My Drive", item_id=MY_DRIVE, owner=AGENCY, shared=True)
    fake.seed_folder("DEV - ALL ACCOUNTS", MY_DRIVE, item_id=ROOT, owner=AGENCY)
    users._repository().signed_in(users.agency_id(), "", f"fake-refresh:{AGENCY}")
    users.set_drive_root(users.agency_id(), ROOT)  # as a first sweep would
    return fake


def _client() -> TestClient:
    """The agency (an admin): its own cards -- the phones `_phone` makes are
    the agency's. Nobody sees another person's: test_ownership.py."""
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
    assert drive.item(account).owner == AGENCY


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
    account = drive.seed_folder("ann.ig - Anna", ROOT, owner=AGENCY)
    day = drive.seed_folder("2026-10-21", account, owner=AGENCY)
    upload = drive.add_file(day, "reel.mp4", "video/mp4")
    store.save_account_folder(login, account, "ann.ig - Anna", ROOT)
    assert store.account_folders()[login].posted_folder_id is None

    report = drive_folders.sync()

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
    assert store.account_folders()[login].posted_folder_id == "posted-a"

    store.save_account_folder(login, "folder-b", "ann.ig - Anna", ROOT)

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


def test_a_new_account_folder_starts_its_posted_history_at_zero(drive):
    """Posted counts follow the folder the files were seen in: a dev copy of
    the prod database must not show prod's history on its own new folders."""
    login = _login(_phone("Phone 01"), "instagram", "ann.ig")
    drive_folders.sync()
    drive.add_file(_posted(drive, "ann.ig"), "reel.mp4", "video/mp4")
    drive_folders.sync()
    assert drive_folders.board().cards[0].posted_today == 1

    store.forget_account_folder(login)
    drive.rename_by_hand(drive.named("ann.ig - Anna", ROOT)[0], "old ann")
    assert drive_folders.sync().created == ["ann.ig - Anna"]

    card = drive_folders.board().cards[0]
    assert (card.posted_total, card.posted_today, card.posted_week) == (0, 0, 0)


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
    html = _client().get("/drive/live").text
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

    def slow_create(parent: str, name: str, **who):
        inside.set()
        assert release.wait(10)
        return real_create(parent, name, **who)

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


# --- the page -------------------------------------------------------------------------


def test_page_shows_what_waits_and_what_was_posted(drive):
    ann = _login(_phone("Phone 01"), "instagram", "ann.ig")
    _login(_phone("Phone 02", owner="Bob"), "instagram", "bob.ig")
    drive_folders.sync()
    account = _account(drive, "ann.ig")
    video = drive.add_file(account, "reel1.mp4", "video/mp4", thumbnail=True)
    drive.add_file(account, "notes.txt", "text/plain")
    drive.add_file(_posted(drive, "ann.ig"), "old.mp4", "video/mp4")
    drive.seed_folder("old messy folder", ROOT)
    drive_folders.sync()

    html = _client().get("/drive/live").text

    # No page-level summary: each card says what waits and what was posted.
    assert "waiting to post in" not in html
    assert "Posted today: <strong>1</strong>" in html
    assert f'src="/drive/thumb/{ann}/{video}"' in html
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

    html = _client().get("/drive/live").text

    assert "2026-10-21" not in html
    assert "Nothing waiting to post" in html
    assert drive.writes == []
    assert drive.calls.count("children") == 1  # the root
    assert drive.calls.count("children_of") == 2  # accounts, POSTED folders


def test_page_lists_every_account_in_a_fixed_number_of_calls(drive):
    """Root cause of a 2-8 s /drive (prod, 2026-10-22): three Drive calls
    per account. Now the root once, then one batch each for the account
    folders and their POSTED folders, however many accounts there are."""
    for n in range(6):
        _login(_phone(f"Phone {n:02d}"), "instagram", f"user{n}.ig")
    drive_folders.sync()
    for n in range(6):
        drive.add_file(_account(drive, f"user{n}.ig"), f"v{n}.mp4", "video/mp4")
    drive.clear_calls()

    html = _client().get("/drive/live").text

    assert html.count('class="dr-file"') == 6
    # The root's own check runs alongside its listing: order not fixed.
    assert sorted(drive.calls) == ["children", "children_of", "children_of", "folder"]


def test_a_trashed_account_folder_is_still_told_on_the_page(drive):
    """Missing from the root listing, it goes the slow way, which tells it
    is gone and forgets it."""
    _login(_phone("Phone 01"), "instagram", "ann.ig")
    _login(_phone("Phone 02", owner="Bea"), "instagram", "bea.ig")
    drive_folders.sync()
    drive.trash(_account(drive, "ann.ig"))

    html = _client().get("/drive/live").text

    assert "The account&#39;s folder was deleted" in html
    assert "Nothing waiting to post" in html  # bea's, from the batch


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

    html = _client().get("/drive/live").text

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

    html = _client().get("/drive/live").text

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


def _uploaded_preview(drive: FakeDrive) -> str:
    """A preview-able file in ann.ig's folder, as "<login>/<file>"."""
    login = _login(_phone("Phone 01"), "instagram", "ann.ig")
    drive_folders.sync()
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
    """The preview image is kept on disk; whether the file is still ours is
    asked every time."""
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
    drive_folders.sync()
    file_id = drive.add_file(_account(drive, "ann.ig"), "a.txt", "text/plain")
    assert _client().get(f"/drive/thumb/{login}/{file_id}").status_code == 404


def test_thumb_route_refuses_a_random_id(drive):
    login = _uploaded_preview(drive).split("/")[0]
    response = _client().get(f"/drive/thumb/{login}/RandomFileId0123456789")
    assert response.status_code == 404
    assert "thumbnail" not in drive.calls


def test_thumb_route_refuses_a_file_outside_the_apps_folders(drive):
    """The owner's account sees their whole Drive: a file kept anywhere
    but that login's folders is not the page's to show."""
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


# --- each owner's own root ------------------------------------------------------


def test_the_agencys_hand_made_root_is_adopted_in_any_case(
    fake_externals, tmp_path, monkeypatch
):
    """Prod's "all accounts" (lower case, made by hand, with folders in it)
    is the owner's root from the first sweep on: nothing is made beside it.
    Production's root name without claiming production (fake externals
    refuse OFM_ENV=production)."""
    monkeypatch.setattr(drive_folders, "_DEV_PREFIX", "")
    monkeypatch.setenv("OFM_UPLOADS_ROOT", str(tmp_path))
    fake = fake_drive()
    fake.seed_folder("My Drive", item_id=MY_DRIVE, owner=AGENCY, shared=True)
    root = fake.seed_folder("all accounts", MY_DRIVE, owner=AGENCY)
    existing = fake.seed_folder("ann.ig - Anna", root)
    users._repository().signed_in(users.agency_id(), "", f"fake-refresh:{AGENCY}")
    _login(_phone("Phone 01"), "instagram", "ann.ig")

    report = drive_folders.sync()

    assert users.get_user(users.agency_id()).drive_root_id == root
    assert report.adopted == ["ann.ig - Anna"]
    assert fake.named("ann.ig - Anna", root) == [existing]
    assert fake.named("ALL ACCOUNTS", MY_DRIVE) == []


def test_dev_never_adopts_the_live_root_it_makes_its_own(
    fake_externals, tmp_path, monkeypatch
):
    """Same Google account on dev and prod: dev works in "DEV - ALL
    ACCOUNTS" only, the live folders are never read or written."""
    monkeypatch.setenv("OFM_UPLOADS_ROOT", str(tmp_path))
    fake = fake_drive()
    fake.seed_folder("My Drive", item_id=MY_DRIVE, owner=AGENCY, shared=True)
    live = fake.seed_folder("ALL ACCOUNTS", MY_DRIVE, owner=AGENCY)
    fake.seed_folder("ann.ig - Anna", live)
    users._repository().signed_in(users.agency_id(), "", f"fake-refresh:{AGENCY}")
    _login(_phone("Phone 01"), "instagram", "ann.ig")

    drive_folders.sync()

    (dev_root,) = fake.named("DEV - ALL ACCOUNTS", MY_DRIVE)
    assert users.get_user(users.agency_id()).drive_root_id == dev_root
    assert fake.named("ann.ig - Anna", dev_root)
    assert len(fake.named("ann.ig - Anna", live)) == 1  # untouched


def test_a_root_trashed_by_hand_is_found_or_made_again(drive):
    _login(_phone("Phone 01"), "instagram", "ann.ig")
    drive_folders.sync()
    drive.trash(ROOT)

    drive_folders.sync()

    (again,) = drive.named("DEV - ALL ACCOUNTS", MY_DRIVE)
    assert again != ROOT
    assert drive.named("ann.ig - Anna", again)


def test_an_owner_without_google_drive_gets_nothing_made_and_is_told(drive):
    from signin import make_user, sign_in

    vee = make_user("vee@example.com")
    phone = devices.add_device(
        DeviceFields(
            label="Vee 01", model_id="iphone-13", color_id="blue", owner="Vee"
        ),
        user_id=vee.id,
    )
    _login(phone, "instagram", "vee.ig")
    drive.clear_calls()

    report = drive_folders.sync()

    assert report.not_connected == ["vee@example.com"]
    assert drive.writes == []
    vee_client = TestClient(app)
    sign_in(vee_client, "vee@example.com")
    html = vee_client.get("/drive/live").text
    assert "vee@example.com has not connected Google Drive" in html


def test_the_folder_owners_files_are_theirs(drive):
    """Made by the owner's own account, not a bot."""
    _login(_phone("Phone 01"), "instagram", "ann.ig")
    drive_folders.sync()
    assert drive.item(_account(drive, "ann.ig")).owner == AGENCY
    assert drive.item(_posted(drive, "ann.ig")).owner == AGENCY


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
    html = _client().get("/drive/live").text
    order = [html.index(f"<h2>{h}</h2>") for h in ("big.ig", "small.ig", "new.ig")]
    assert order == sorted(order)
    assert "90k" not in html
    assert "90000" not in html


# --- the page opens from the last read ---------------------------------------------


def test_the_page_opens_with_no_drive_call_from_the_last_read(drive):
    """A click on /drive waited ~2 s for Google every time. Now it shows
    what the last read (sweep, Sync, a live load) saw, and drive.js fetches
    /drive/live behind it."""
    _login(_phone("Phone 01"), "instagram", "ann.ig")
    drive_folders.sync()  # reads every folder: the page has something to show
    drive.add_file(_account(drive, "ann.ig"), "new.mp4", "video/mp4")
    drive.clear_calls()

    html = _client().get("/drive").text

    assert drive.calls == []
    assert 'data-live="/drive/live"' in html
    assert 'class="dr-asof"' in html
    assert "Nothing waiting to post" in html  # the sweep saw no file yet

    live = _client().get("/drive/live").text
    assert "new.mp4" in live
    assert 'class="dr-asof"' not in live
    # The live read is the next page's start.
    drive.clear_calls()
    assert "new.mp4" in _client().get("/drive").text
    assert drive.calls == []


def test_a_folder_never_read_says_so_until_the_live_read(drive):
    """A folder made but not read yet (as on a fresh install, before the
    first sweep finished)."""
    login = _login(_phone("Phone 01"), "instagram", "ann.ig")
    folder = drive.create_folder(ROOT, "ann.ig - Anna")
    store.save_account_folder(login, folder.id, "ann.ig - Anna", ROOT)

    html = _client().get("/drive").text
    assert "Reading from Drive" in html
    assert "Reading from Drive" not in _client().get("/drive/live").text


# --- review fixes: scope, the live root in a dev copy, batch 404s ----------------------


def _user_phone(email: str, handle: str, owner: str, *, google: bool = True) -> int:
    """A user with a phone holding one Instagram login; Google connected."""
    from signin import make_user

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
    from signin import sign_in

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
    assert users.by_email("bob@example.com").drive_root_id is None  # untouched

    admin = _client().post("/drive/sync").json()  # an admin's: their own too
    assert admin["created"] == []
    assert admin["not_connected"] == []
    assert users.by_email("bob@example.com").drive_root_id is None

    sweep = drive_folders.sync()  # the background loop: everyone's
    assert sweep.created == ["bob.ig - Bob"]
    assert sweep.not_connected == ["cy@example.com"]


def test_dev_never_uses_a_stored_live_root_from_a_prod_copy(drive):
    """A dev database copied from prod keeps the live "ALL ACCOUNTS" id and
    its folder rows: dev finds its own root by name, never writes or reads
    in the live one, and the live rows are not used."""
    login = _login(_phone("Phone 01"), "instagram", "ann.ig")
    live = drive.seed_folder("ALL ACCOUNTS", MY_DRIVE, owner=AGENCY)
    live_account = drive.seed_folder("ann.ig - hand named", live, owner=AGENCY)
    live_posted = drive.seed_folder(POSTED, live_account, owner=AGENCY)
    store.save_account_folder(login, live_account, "ann.ig - Anna", live)
    store.save_posted_folder(login, live_posted)
    users.set_drive_root(users.agency_id(), live)

    report = drive_folders.sync()

    assert users.get_user(users.agency_id()).drive_root_id == ROOT
    assert report.created == ["ann.ig - Anna"]
    assert _stored().folder_id == _account(drive, "ann.ig")
    assert drive.item(live).name == "ALL ACCOUNTS"
    assert drive.names_in(live) == ["ann.ig - hand named"]  # not renamed back
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
    assert store.account_folders() == {}


def test_a_404_in_the_batch_keeps_the_root_and_lists_one_by_one(drive, monkeypatch):
    """An account folder deleted between the root listing and the batch
    404s the whole `children_of` query: the healthy root stays."""
    _login(_phone("Phone 01"), "instagram", "ann.ig")
    drive_folders.sync()
    drive.add_file(_account(drive, "ann.ig"), "new.mp4", "video/mp4")

    def gone(_parent_ids):
        msg = "Folder x: not found"
        raise FolderGoneError(msg)

    monkeypatch.setattr(drive, "children_of", gone)

    html = _client().get("/drive/live").text

    assert users.get_user(users.agency_id()).drive_root_id == ROOT
    assert "new.mp4" in html
