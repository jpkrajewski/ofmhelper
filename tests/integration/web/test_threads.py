"""/threads: an account's POSTED reels; Make pictures / Regenerate makes a
set of a reel (stills as the phone's own photos) into its own folder in
THREADS in the account's Drive folder. Review 2026-11-18: thirty clicks at
once start one set, a set never shares a folder, a rerun uploads nothing
twice, a picture or a missing reel is refused, no Drive is refused before
anything is queued. The photo writer itself: tests/unit/repurpose."""

import os
from concurrent.futures import ThreadPoolExecutor

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ.setdefault("SESSION_SECRET", "test-secret")

import pytest
from fastapi.testclient import TestClient
from signin import sign_in_agency

from ofmhelpers.cache import get_redis
from ofmhelpers.db.stores import devices, users
from ofmhelpers.db.stores.jobs import get_job, list_batch
from ofmhelpers.features.drive import folders as drive_folders
from ofmhelpers.features.jobs import registry
from ofmhelpers.features.threads import service, tasks
from ofmhelpers.integrations.gdrive.fake import FakeDrive, fake_drive
from ofmhelpers.media.repurpose.photos.models import Photo
from ofmhelpers.schemas import DeviceFields, LoginFields
from ofmhelpers.web.main import app

AGENCY = "agency@example.com"
MY_DRIVE = f"mydrive-{AGENCY}"
ROOT = "agency-root-folder"


@pytest.fixture
def drive(fake_externals, monkeypatch, tmp_path) -> FakeDrive:
    """The fake Drive, the agency's Google Drive connected with its
    "DEV - ALL ACCOUNTS" root (as /trials' tests)."""
    monkeypatch.setenv("OFM_UPLOADS_ROOT", str(tmp_path))
    fake = fake_drive()
    fake.seed_folder("My Drive", item_id=MY_DRIVE, owner=AGENCY, shared=True)
    fake.seed_folder("DEV - ALL ACCOUNTS", MY_DRIVE, item_id=ROOT, owner=AGENCY)
    users._repository().signed_in(users.agency_id(), "", f"fake-refresh:{AGENCY}")
    users.set_drive_root(users.agency_id(), ROOT)
    return fake


@pytest.fixture
def photos(monkeypatch) -> list[str]:
    """The photo writer faked: three small files a set; the seeds it ran
    with, in order."""
    seeds: list[str] = []

    def write(src, dest, *, preset, seed, timeout_s):
        seeds.append(seed)
        written = []
        for i, path in enumerate(dest[:3]):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(f"{seed}-{i}".encode())
            written.append(Photo(path=path, at=float(i), crop=0.05, bits=90))
        return written

    monkeypatch.setattr(tasks, "write_photos", write)
    return seeds


@pytest.fixture
def real_queue(monkeypatch):
    """Jobs wait in Redis from here on, instead of running inline."""

    def switch_on() -> None:
        monkeypatch.setenv("OFM_RQ_ASYNC", "true")
        get_redis().flushdb()

    yield switch_on
    get_redis().flushdb()


def _client() -> TestClient:
    c = TestClient(app)
    sign_in_agency(c)
    return c


def _account(drive: FakeDrive) -> str:
    (account,) = drive.named("ann.ig - Anna", ROOT)
    return account


def _login(drive: FakeDrive, *files: tuple[str, str]) -> int:
    """A phone with @ann.ig, its folders made, `files` (name, mime) in its
    POSTED and read by a sweep (what /threads shows)."""
    phone = devices.add_device(
        DeviceFields(
            label="Phone 01", model_id="iphone-13", color_id="blue", owner="Anna"
        )
    )
    added = devices.add_login(
        phone, LoginFields(service="instagram", username="ann.ig")
    )
    assert added is not None
    drive_folders.sync()
    (posted,) = drive.named("POSTED", _account(drive))
    for name, mime in files:
        drive.add_file(posted, name, mime)
    drive_folders.sync()
    return added.id


def _key(drive: FakeDrive, name: str) -> str:
    (posted,) = drive.named("POSTED", _account(drive))
    (key,) = drive.named(name, posted)
    return key


def _sets(login_id: int) -> list:
    return list_batch(tasks.TASK, str(login_id), str(users.agency_id()))


def test_the_page_lists_the_posted_reels_to_make_pictures_of(drive):
    login_id = _login(drive, ("VID_1.mp4", "video/mp4"), ("IMG_1.jpg", "image/jpeg"))

    page = _client().get(f"/threads/{login_id}")

    assert page.status_code == 200
    assert "VID_1.mp4" in page.text
    assert "IMG_1.jpg" not in page.text
    assert "Make pictures" in page.text


def test_thirty_clicks_at_once_start_one_set(drive, real_queue):
    """Two tabs, a retried request: before the lock, 30 POSTs made 30 jobs
    (review 2026-11-18)."""
    login_id = _login(drive, ("VID_1.mp4", "video/mp4"))
    url = f"/threads/{login_id}/reels/{_key(drive, 'VID_1.mp4')}/pictures"
    real_queue()
    client = _client()

    with ThreadPoolExecutor(max_workers=30) as pool:
        codes = list(
            pool.map(
                lambda _: client.post(url, follow_redirects=False).status_code,
                range(30),
            )
        )

    assert set(codes) == {303}
    assert len(_sets(login_id)) == 1


def test_a_picture_or_a_reel_not_in_posted_is_refused(drive, photos):
    login_id = _login(drive, ("IMG_1.jpg", "image/jpeg"))
    client = _client()

    picture = client.post(
        f"/threads/{login_id}/reels/{_key(drive, 'IMG_1.jpg')}/pictures"
    )
    unknown = client.post(f"/threads/{login_id}/reels/nope/pictures")

    assert (picture.status_code, unknown.status_code) == (404, 404)
    assert _sets(login_id) == []
    assert photos == []


def test_without_google_drive_nothing_is_queued(drive, photos, monkeypatch):
    login_id = _login(drive, ("VID_1.mp4", "video/mp4"))
    monkeypatch.setattr(service, "refresh_token", lambda _uid: "")

    answer = _client().post(
        f"/threads/{login_id}/reels/{_key(drive, 'VID_1.mp4')}/pictures"
    )

    assert answer.status_code == 400
    assert answer.json() == {"detail": service.NO_DRIVE}
    assert _sets(login_id) == []


def test_each_set_has_its_own_folder_and_a_rerun_uploads_nothing_twice(drive, photos):
    """A set keeps the folder it made (its id in the job's params): two sets
    of one reel never mix, and the same set run again (recovery) finds its
    pictures on disk and in its folder."""
    login_id = _login(drive, ("VID_1.mp4", "video/mp4"))
    url = f"/threads/{login_id}/reels/{_key(drive, 'VID_1.mp4')}/pictures"
    client = _client()

    client.post(url)
    client.post(url)

    first, second = _sets(login_id)
    assert (first.status.value, second.status.value) == ("done", "done")
    assert first.params["seed"] != second.params["seed"]
    (threads,) = drive.named("THREADS", _account(drive))
    assert sorted(drive.names_in(threads)) == ["VID_1", "VID_1 (2)"]
    (one,) = drive.named("VID_1", threads)
    assert first.params["folder_id"] == one
    assert len(drive.names_in(one)) == 3

    job = get_job(first.id)
    assert job is not None
    tasks.make_pictures(**registry._threads(job))

    assert len(photos) == 2  # nothing cut again
    assert len(drive.names_in(one)) == 3  # nothing uploaded again
    assert sorted(drive.names_in(threads)) == ["VID_1", "VID_1 (2)"]


def test_a_set_whose_folder_was_deleted_by_hand_makes_another(drive, photos):
    login_id = _login(drive, ("VID_1.mp4", "video/mp4"))
    _client().post(f"/threads/{login_id}/reels/{_key(drive, 'VID_1.mp4')}/pictures")
    (made,) = _sets(login_id)
    drive.delete(made.params["folder_id"])

    job = get_job(made.id)
    assert job is not None
    result = tasks.make_pictures(**registry._threads(job))

    (threads,) = drive.named("THREADS", _account(drive))
    (again,) = drive.named("VID_1", threads)
    assert again != made.params["folder_id"]
    assert len(drive.names_in(again)) == 3
    assert len(result.names) == 3
