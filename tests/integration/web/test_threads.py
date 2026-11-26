# Copyright (C) 2026 jpkrajewski

from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from notifications_socket import SOCKET, next_lines, next_sent
from signin import sign_in_agency

from ofmhelpers.background import registry
from ofmhelpers.cache import get_redis
from ofmhelpers.db.devices import store as devices
from ofmhelpers.db.devices.models import DeviceFields, LoginFields
from ofmhelpers.db.jobs.store import get_job, list_batch
from ofmhelpers.db.users import store as users
from ofmhelpers.features.storage import jobs as storage_jobs
from ofmhelpers.features.storage.client import storage_of
from ofmhelpers.features.threads import service, tasks
from ofmhelpers.integrations.storage.gdrive.fake import FakeDrive, fake_drive
from ofmhelpers.media.repurpose.photos.models import Photo
from ofmhelpers.web.main import app

AGENCY = "agency@example.com"
MY_DRIVE = f"mydrive-{AGENCY}"
ROOT = "agency-root-folder"


@pytest.fixture
def drive(fake_externals, monkeypatch, tmp_path) -> FakeDrive:
    monkeypatch.setenv("OFM_UPLOADS_ROOT", str(tmp_path))
    fake = fake_drive()
    fake.seed_folder("My Drive", item_id=MY_DRIVE, owner=AGENCY, shared=True)
    fake.seed_folder("DEV - ALL ACCOUNTS", MY_DRIVE, item_id=ROOT, owner=AGENCY)
    users._repository().signed_in(users.agency_id(), "", f"fake-refresh:{AGENCY}")
    users.set_drive_root(users.agency_id(), ROOT)
    return fake


@pytest.fixture
def photos(monkeypatch) -> list[str]:
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
    phone = devices.add_device(
        DeviceFields(
            label="Phone 01",
            model_id="iphone-13",
            color_id="blue",
            owner="Anna",
        ),
    )
    added = devices.add_login(
        phone,
        LoginFields(service="instagram", username="ann.ig"),
    )
    assert added is not None
    storage_jobs.sync()
    (posted,) = drive.named("POSTED", _account(drive))
    for name, mime in files:
        drive.add_file(posted, name, mime)
    storage_jobs.sync()
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
    login_id = _login(drive, ("VID_1.mp4", "video/mp4"))
    url = f"/threads/{login_id}/reels/{_key(drive, 'VID_1.mp4')}/pictures"
    real_queue()
    client = _client()

    with ThreadPoolExecutor(max_workers=30) as pool:
        codes = list(
            pool.map(
                lambda _: client.post(url, follow_redirects=False).status_code,
                range(30),
            ),
        )

    assert set(codes) == {303}
    assert len(_sets(login_id)) == 1


def test_a_picture_or_a_reel_not_in_posted_is_refused(drive, photos):
    login_id = _login(drive, ("IMG_1.jpg", "image/jpeg"))
    client = _client()

    picture = client.post(
        f"/threads/{login_id}/reels/{_key(drive, 'IMG_1.jpg')}/pictures",
    )
    unknown = client.post(f"/threads/{login_id}/reels/nope/pictures")

    assert (picture.status_code, unknown.status_code) == (404, 404)
    assert _sets(login_id) == []
    assert photos == []


def test_without_google_drive_nothing_is_queued(drive, photos, monkeypatch):
    login_id = _login(drive, ("VID_1.mp4", "video/mp4"))
    monkeypatch.setattr(service, "refresh_token", lambda _uid: "")

    answer = _client().post(
        f"/threads/{login_id}/reels/{_key(drive, 'VID_1.mp4')}/pictures",
    )

    assert answer.status_code == 400
    assert answer.json() == {"detail": service.NO_DRIVE}
    assert _sets(login_id) == []


def test_each_set_has_its_own_folder_and_a_rerun_uploads_nothing_twice(drive, photos):
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
    tasks.make_pictures(**registry._threads(job).model_dump())

    assert len(photos) == 2
    assert len(drive.names_in(one)) == 3
    assert sorted(drive.names_in(threads)) == ["VID_1", "VID_1 (2)"]


def test_a_set_whose_folder_was_deleted_by_hand_makes_another(drive, photos):
    login_id = _login(drive, ("VID_1.mp4", "video/mp4"))
    _client().post(f"/threads/{login_id}/reels/{_key(drive, 'VID_1.mp4')}/pictures")
    (made,) = _sets(login_id)
    drive.delete(made.params["folder_id"])

    job = get_job(made.id)
    assert job is not None
    result = tasks.make_pictures(**registry._threads(job).model_dump())

    (threads,) = drive.named("THREADS", _account(drive))
    (again,) = drive.named("VID_1", threads)
    assert again != made.params["folder_id"]
    assert len(drive.names_in(again)) == 3
    assert len(result.names) == 3


def test_the_index_shows_generated_next_to_posted(drive, photos):
    login_id = _login(drive, ("VID_1.mp4", "video/mp4"))
    uid = users.agency_id()
    folders = storage_of(uid, interactive=True).folders
    threads = service.Threads(uid=uid, folders=folders, login_id=login_id)
    threads.start(_key(drive, "VID_1.mp4"))

    page = _client().get("/threads")

    assert page.status_code == 200
    assert "Posted: <strong>1</strong>" in page.text
    assert "Generated: <strong>1</strong>" in page.text
    assert "Generating: <strong>0</strong>" in page.text


def test_the_index_shows_what_is_generating(drive, photos, real_queue):
    login_id = _login(drive, ("VID_1.mp4", "video/mp4"))
    real_queue()
    client = _client()
    client.post(
        f"/threads/{login_id}/reels/{_key(drive, 'VID_1.mp4')}/pictures",
        follow_redirects=False,
    )

    page = client.get("/threads")

    assert "Generating: <strong>1</strong>" in page.text
    assert "Generated: <strong>0</strong>" in page.text


def test_a_set_that_ends_is_on_its_persons_open_notifications_at_once(drive, photos):
    login_id = _login(drive, ("VID_1.mp4", "video/mp4"))
    uid = users.agency_id()
    folders = storage_of(uid, interactive=True).folders
    threads = service.Threads(uid=uid, folders=folders, login_id=login_id)
    with _client().websocket_connect(SOCKET) as socket:
        assert next_sent(socket) == []

        threads.start(_key(drive, "VID_1.mp4"))

        (line,) = next_lines(socket)
    assert (line["title"], line["text"], line["url"], line["kind"]) == (
        "@ann.ig",
        "Threads pictures ready: VID_1.mp4",
        f"/threads/{login_id}",
        "info",
    )
