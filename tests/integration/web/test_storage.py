# Copyright (C) 2026 jpkrajewski

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
lazy from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError as RedisConnectionError
from signin import sign_in_agency

from ofmhelpers.cache import get_redis
from ofmhelpers.config.types import AccountFolder
from ofmhelpers.db.devices import store as devices
from ofmhelpers.db.devices.models import DeviceFields, LoginFields
from ofmhelpers.db.jobs.schema import Job, JobStatus
from ofmhelpers.db.jobs.store import latest_job
from ofmhelpers.db.users import store as users
from ofmhelpers.features.storage import bin as storage_bin
from ofmhelpers.features.storage import jobs as storage_jobs
from ofmhelpers.features.storage.client import storage_of
from ofmhelpers.features.storage.constants import StorageConstants, StorageTask
from ofmhelpers.features.storage.jobs import run_bin
from ofmhelpers.features.storage.models import Moved, StorageUsage
from ofmhelpers.features.storage.service import StorageServiceBin
from ofmhelpers.integrations.storage import DriveError
from ofmhelpers.integrations.storage.gdrive.fake import FakeDrive, fake_drive
from ofmhelpers.web.main import app

AGENCY = "agency@example.com"
MY_DRIVE = f"mydrive-{AGENCY}"
ROOT = "agency-root-folder"
MB = 1_000_000


@pytest.fixture
def drive(fake_externals, monkeypatch, tmp_path) -> FakeDrive:
    monkeypatch.setenv("OFM_UPLOADS_ROOT", str(tmp_path))
    fake = fake_drive()
    fake.seed_folder("My Drive", item_id=MY_DRIVE, owner=AGENCY, shared=True)
    fake.seed_folder("DEV - ALL ACCOUNTS", MY_DRIVE, item_id=ROOT, owner=AGENCY)
    users._repository().signed_in(users.agency_id(), "", f"fake-refresh:{AGENCY}")
    users.set_drive_root(users.agency_id(), ROOT)
    fake.set_storage(15_000 * MB, used=700 * MB)
    return fake


@pytest.fixture
def real_queue(monkeypatch):

    def switch_on() -> None:
        monkeypatch.setenv("OFM_RQ_ASYNC", "true")
        get_redis().flushdb()

    yield switch_on
    get_redis().flushdb()


class Account:
    def __init__(self, drive: FakeDrive, folder_id: str) -> None:
        self.drive = drive
        self.id = folder_id

    def part(self, name: AccountFolder) -> str:
        return self.drive.seed_folder(name, self.id, owner=AGENCY)

    def file(self, parent: str, name: str, megabytes: int) -> str:
        return self.drive.add_file(
            parent,
            name,
            "video/mp4",
            owner=AGENCY,
            size=megabytes * MB,
        )


def _accounts(drive: FakeDrive, *handles: str) -> list[Account]:
    phone = devices.add_device(
        DeviceFields(
            label="Phone 01",
            model_id="iphone-13",
            color_id="blue",
            owner="Anna",
        ),
    )
    for handle in handles:
        assert devices.add_login(
            phone,
            LoginFields(service="instagram", username=handle),
        )
    storage_jobs.sync()
    return [Account(drive, drive.named(f"{h} - Anna", ROOT)[0]) for h in handles]


def _client() -> TestClient:
    client = TestClient(app)
    sign_in_agency(client)
    return client


def _storage() -> StorageServiceBin:
    owner = users.agency_id()
    return storage_of(owner, interactive=True).bin


def _actor() -> str:
    return str(users.agency_id())


def _job(task: str, status: JobStatus | None = None) -> Job:
    found = latest_job(task, _actor(), status)
    assert found is not None
    return found


def _scan(status: JobStatus | None = None) -> Job:
    return _job(StorageTask.SCAN, status)


def _move() -> Job:
    return _job(StorageTask.BIN)


def _laid_out(drive: FakeDrive) -> dict[str, str]:
    ann, bob = _accounts(drive, "ann.ig", "bob.ig")
    trials = ann.part(AccountFolder.TRIALS)
    threads = ann.part(AccountFolder.THREADS)
    reel = drive.seed_folder("VID_1", threads, owner=AGENCY)
    bob_trials = bob.part(AccountFolder.TRIALS)
    return {
        "ann": ann.id,
        "bob": bob.id,
        "ann_trials": trials,
        "ann_trial_1": ann.file(trials, "VID_1001.mp4", 10),
        "ann_trial_2": ann.file(trials, "VID_1002.mp4", 20),
        "ann_spoofed": ann.file(ann.part(AccountFolder.SPOOFED), "spoof.mp4", 5),
        "ann_reel": reel,
        "ann_picture": ann.file(reel, "IMG_9001.JPG", 2),
        "ann_waiting": ann.file(ann.id, "waiting.mp4", 7),
        "bob_trial": bob.file(bob_trials, "VID_2001.mp4", 30),
    }


def test_the_page_shows_each_account_folder_and_what_its_parts_hold(drive):
    _laid_out(drive)

    page = _client().get("/storage")

    assert page.status_code == 200
    usage = StorageUsage.model_validate(_scan(JobStatus.done).result)
    sizes = {f.name: f.sizes for f in usage.folders}
    ann, bob = sizes["ann.ig - Anna"], sizes["bob.ig - Anna"]
    assert (ann.total, bob.total) == (44 * MB, 30 * MB)
    assert ann.of(AccountFolder.TRIALS) == 30 * MB
    assert ann.of(AccountFolder.SPOOFED) == 5 * MB
    assert ann.of(AccountFolder.THREADS) == 2 * MB
    assert bob.of(AccountFolder.THREADS) == 0
    assert usage.quota.usage == 700 * MB
    assert "ann.ig - Anna" in page.text
    assert "30.0 MB" in page.text
    assert "Move all trials to bin" in page.text


def test_one_folders_trials_go_to_the_bin_and_nothing_else_moves(drive):
    ids = _laid_out(drive)

    answer = _client().post(
        "/storage/bin-moves",
        data={"part": "TRIALS", "folder_id": ids["ann"]},
    )

    assert answer.status_code == 202
    moved = Moved.model_validate(_move().result)
    assert (moved.items, moved.bytes) == (2, 30 * MB)
    for key in ("ann_trial_1", "ann_trial_2"):
        found = drive.item(ids[key])
        assert found is not None
        assert found.trashed
    for key in ("ann_trials", "ann_spoofed", "ann_picture", "ann_waiting", "bob_trial"):
        found = drive.item(ids[key])
        assert found is not None
        assert not found.trashed


def test_every_folders_threads_go_to_the_bin_with_their_pictures(drive):
    ids = _laid_out(drive)

    moved = run_bin(users.agency_id(), "test", "THREADS")

    assert (moved.items, moved.bytes, moved.folder_id) == (1, 2 * MB, None)
    for key in ("ann_reel", "ann_picture"):
        found = drive.item(ids[key])
        assert found is not None
        assert found.trashed
    trial = drive.item(ids["ann_trial_1"])
    assert trial is not None
    assert not trial.trashed


def test_run_again_it_moves_nothing_more_and_deletes_nothing(drive):
    ids = _laid_out(drive)
    run_bin(users.agency_id(), "test", "TRIALS")

    again = run_bin(users.agency_id(), "test", "TRIALS")

    assert (again.items, again.bytes) == (0, 0)
    assert all(drive.item(item) is not None for item in ids.values())


def test_a_folder_that_is_not_one_of_the_owners_is_refused(drive):
    _laid_out(drive)

    answer = _client().post(
        "/storage/bin-moves",
        data={"part": "TRIALS", "folder_id": "1" + "a" * 32},
    )
    junk = _client().post("/storage/bin-moves", data={"part": "POSTED"})

    assert (answer.status_code, junk.status_code) == (404, 422)
    assert latest_job(StorageTask.BIN, _actor()) is None


def test_twenty_clicks_at_once_start_one_move(drive, real_queue):
    _laid_out(drive)
    client = _client()
    real_queue()

    with ThreadPoolExecutor(max_workers=20) as pool:
        codes = sorted(
            pool.map(
                lambda _: (
                    client.post(
                        "/storage/bin-moves",
                        data={"part": "SPOOFED"},
                    ).status_code
                ),
                range(20),
            ),
        )

    assert codes == [202] + [409] * 19


def test_without_google_drive_the_page_asks_to_connect_and_nothing_runs(drive):
    users._repository().update(users.agency_id(), {"google_refresh_token": ""})

    page = _client().get("/storage")
    answer = _client().post("/storage/bin-moves", data={"part": "TRIALS"})

    assert "Connect Google Drive" in page.text
    assert answer.status_code == 400
    assert latest_job(StorageTask.SCAN, _actor()) is None


def test_drive_failing_keeps_the_reason_on_the_job_and_the_page_says_so(
    drive,
    monkeypatch,
):
    _laid_out(drive)

    def down(_self: FakeDrive) -> None:
        msg = "Storage failed: 500 Internal Error"
        raise DriveError(msg)

    monkeypatch.setattr(FakeDrive, "storage", down)

    page = _client().get("/storage")

    scan = _scan()
    assert scan.status is JobStatus.failed
    assert scan.error == "Storage failed: 500 Internal Error"
    assert "Could not read Google Drive" in page.text


def test_a_server_that_only_reads_the_live_drive_refuses_to_move(drive, monkeypatch):
    ids = _laid_out(drive)
    monkeypatch.setenv("OFM_DRIVE_READ_LIVE", "1")

    answer = _client().post(
        "/storage/bin-moves",
        data={"part": "TRIALS", "folder_id": ids["ann"]},
    )

    assert answer.status_code == 400
    assert answer.json() == {"detail": StorageConstants().read_only}
    assert latest_job(StorageTask.BIN, _actor()) is None


def test_with_the_queue_down_the_page_still_shows_the_last_scan(drive, monkeypatch):
    _laid_out(drive)
    _storage().page()
    _client().post("/storage/bin-moves", data={"part": "SPOOFED"})

    def down(*_args: object, **_kwargs: object) -> None:
        msg = "Connection refused"
        raise RedisConnectionError(msg)

    monkeypatch.setattr(storage_bin, "start_job", down)

    page = _client().get("/storage")

    assert page.status_code == 200
    assert "ann.ig - Anna" in page.text
    assert "30.0 MB" in page.text


def test_a_folder_gone_from_the_root_meanwhile_moves_nothing(drive):
    ids = _laid_out(drive)
    drive.trash(ids["ann"])

    moved = run_bin(users.agency_id(), "test", "TRIALS", ids["ann"])

    assert (moved.items, moved.bytes) == (0, 0)
    bob_trial = drive.item(ids["bob_trial"])
    assert bob_trial is not None
    assert not bob_trial.trashed


def test_with_redis_down_a_move_is_refused_and_no_job_is_made(drive, monkeypatch):
    _laid_out(drive)

    @contextmanager
    def unheld(_key: str, _wait_s: int) -> Iterator[bool]:
        yield False

    monkeypatch.setattr(storage_bin, "lock_held", unheld)

    answer = _client().post("/storage/bin-moves", data={"part": "TRIALS"})

    assert answer.status_code == 503
    assert answer.json() == {"detail": StorageConstants().try_again}
    assert latest_job(StorageTask.BIN, _actor()) is None


def test_a_fresh_scan_is_not_started_again_until_it_is_stale(drive):
    _laid_out(drive)
    _storage().page()
    first = _scan()

    _storage().page()

    assert _scan().id == first.id


def test_a_finished_move_starts_a_new_scan(drive):
    _laid_out(drive)
    _storage().page()
    first = _scan()
    _client().post("/storage/bin-moves", data={"part": "TRIALS"})

    view = _storage().page()

    assert _scan().id != first.id
    assert view.usage is not None
    assert view.usage.totals.of(AccountFolder.TRIALS) == 0
    assert view.moved is not None
    assert view.moved.items == 3
