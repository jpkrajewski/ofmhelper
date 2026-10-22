"""/clone "Upload to Drive": a finished run's video goes into an Instagram
account's Drive folder as the clicker's Google account -- once per folder,
only into folders they may use, only their own runs, and a failure is
shown and may be retried."""

import os
import time

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ.setdefault("SESSION_SECRET", "test-secret")

from pathlib import Path

import pytest
import time_machine
from fastapi.testclient import TestClient
from signin import sign_in

from ofmhelpers.gdrive import DriveError
from ofmhelpers.gdrive.fake import fake_drive
from ofmhelpers.web import drive_folders, drive_uploads
from ofmhelpers.web.main import app
from ofmhelpers.web.stores import drive_folders as folder_store
from ofmhelpers.web.stores import users
from ofmhelpers.web.stores.jobs import create_job, run_job
from ofmhelpers.web.stores.users import _repository as user_repository

ROOT = "upload-root-folder"
PHONE = {
    "label": "Phone 01",
    "model_id": "iphone-13",
    "color_id": "blue",
    "owner": "Anna",
}


@pytest.fixture
def drive(fake_externals, monkeypatch, tmp_path):
    monkeypatch.setenv("OFM_GDRIVE_SERVICE_ACCOUNT", "{}")
    monkeypatch.setenv("OFM_GDRIVE_ROOT_FOLDER", ROOT)
    monkeypatch.setenv("OFM_UPLOADS_ROOT", str(tmp_path / "uploads"))
    return fake_drive()


def _client(email: str, *, google: bool = True) -> TestClient:
    c = TestClient(app)
    user = sign_in(c, email)
    if google:
        user_repository().signed_in(user.id, "", f"fake-refresh:{email}")
    return c


def _account_folder(client: TestClient, handle: str = "ann.ig") -> int:
    """A phone with an Instagram login, its Drive folder made by a sweep;
    the login's id."""
    phone = client.post("/phones/add", json=PHONE).json()["id"]
    login = client.post(
        f"/phones/{phone}/logins", json={"service": "instagram", "username": handle}
    ).json()["id"]
    drive_folders.sync()
    return login


def _finished_run(email: str, tmp_path: Path, name: str = "clone.mp4") -> str:
    video = tmp_path / name
    video.write_bytes(b"\x00\x00\x00\x18ftypmp42 fake")
    params = {
        "prompt": "p",
        "resolution": "720P",
        "audio": False,
        "duration": 5,
        "reference_images": [],
        "reference_videos": [str(video)],
        "clone_batch": f"batch-{name}",
        "clone_index": 0,
    }
    job_id = create_job("clone", params, actor=str(users.by_email(email).id))
    run_job(job_id, lambda: [{"name": video.name, "path": str(video)}], {})
    return job_id


def _root_of(email: str) -> str:
    """The person's "DEV - ALL ACCOUNTS" in their own Drive."""
    root = users.by_email(email).drive_root_id
    assert root, f"{email} has no Drive root yet"
    return root


def _files_in(drive, folder_name: str, email: str = "ann@example.com") -> list[str]:
    (folder,) = drive.named(folder_name, _root_of(email))
    return [n for n in drive.names_in(folder) if n != "POSTED"]


def test_a_run_uploads_into_the_account_folder_once(drive, tmp_path):
    ann = _client("ann@example.com")
    login = _account_folder(ann)
    job = _finished_run("ann@example.com", tmp_path)

    r = ann.post("/clone/drive-upload", json={"job_ids": [job], "login_ids": [login]})

    assert r.status_code == 200, r.text
    assert r.json()["queued"] in (0, 1)  # the test worker ran it inline
    [state] = ann.get("/clone/drive-uploads", params={"job": job}).json()
    assert state["status"] == "done"
    assert state["folder_name"] == "ann.ig - Anna"
    assert state["drive_url"]
    [uploaded] = _files_in(drive, "ann.ig - Anna")
    assert uploaded.startswith("VID_")  # the camera-roll name Download uses
    (folder,) = drive.named("ann.ig - Anna", _root_of("ann@example.com"))
    (file_id,) = drive.named(uploaded, folder)
    assert drive.item(file_id).owner == "ann@example.com"  # her own Drive

    again = ann.post(
        "/clone/drive-upload", json={"job_ids": [job], "login_ids": [login]}
    )
    assert again.json()["queued"] == 0
    assert len(_files_in(drive, "ann.ig - Anna")) == 1  # never twice
    assert "In Drive:" in ann.get("/clone").text


def test_without_google_drive_connected_nothing_is_queued(drive, tmp_path):
    ann = _client("ann@example.com", google=False)
    login = _account_folder(ann)
    job = _finished_run("ann@example.com", tmp_path)
    r = ann.post("/clone/drive-upload", json={"job_ids": [job], "login_ids": [login]})
    assert r.status_code == 400
    assert "Connect Google Drive" in r.json()["detail"]
    assert users.by_email("ann@example.com").drive_root_id is None  # nothing made


def test_nobody_uploads_into_anothers_folder_or_anothers_run(drive, tmp_path):
    ann = _client("ann@example.com")
    bob = _client("bob@example.com")
    ann_login = _account_folder(ann)
    ann_job = _finished_run("ann@example.com", tmp_path, "a.mp4")
    bob_job = _finished_run("bob@example.com", tmp_path, "b.mp4")

    r = bob.post(
        "/clone/drive-upload", json={"job_ids": [bob_job], "login_ids": [ann_login]}
    )
    assert r.status_code == 400
    r = ann.post(
        "/clone/drive-upload", json={"job_ids": [bob_job], "login_ids": [ann_login]}
    )
    assert r.json()["uploads"] == []
    assert bob.get("/clone/drive-uploads", params={"job": ann_job}).json() == []
    assert _files_in(drive, "ann.ig - Anna") == []


def test_a_failed_upload_says_why_and_can_be_retried(drive, tmp_path, monkeypatch):
    ann = _client("ann@example.com")
    login = _account_folder(ann)
    job = _finished_run("ann@example.com", tmp_path)

    working = drive_uploads.connect_as

    def refused(_token):
        msg = "Uploading: Drive refused (quota)"
        raise DriveError(msg)

    monkeypatch.setattr(drive_uploads, "connect_as", refused)
    ann.post("/clone/drive-upload", json={"job_ids": [job], "login_ids": [login]})
    [state] = ann.get("/clone/drive-uploads", params={"job": job}).json()
    assert (state["status"], state["error"]) == (
        "failed",
        "Uploading: Drive refused (quota)",
    )
    assert "Drive upload to ann.ig - Anna failed" in ann.get("/clone").text

    monkeypatch.setattr(drive_uploads, "connect_as", working)
    retry = ann.post(
        "/clone/drive-upload", json={"job_ids": [job], "login_ids": [login]}
    )
    assert retry.status_code == 200
    [state] = ann.get("/clone/drive-uploads", params={"job": job}).json()
    assert state["status"] == "done"


def test_the_drive_picker_offers_only_the_users_folders(drive, tmp_path):
    ann = _client("ann@example.com")
    bob = _client("bob@example.com")
    login = _account_folder(ann)
    _finished_run("ann@example.com", tmp_path, "a.mp4")
    _finished_run("bob@example.com", tmp_path, "b.mp4")

    ann_page = ann.get("/clone").text
    assert f'data-login="{login}"' in ann_page
    assert 'data-folder="ann.ig - Anna"' in ann_page
    assert "Upload to Drive" in ann_page  # in the finished card's menu
    bob_page = bob.get("/clone").text
    assert "ann.ig - Anna" not in bob_page
    # Bob has no account folder: no picker, no menu item.
    assert 'id="drive-picker"' not in bob_page
    assert "Upload to Drive" not in bob_page


def test_queueing_into_several_folders_at_once(drive, tmp_path):
    ann = _client("ann@example.com")
    first = _account_folder(ann)
    job = _finished_run("ann@example.com", tmp_path, "a.mp4")
    r = ann.post(
        "/clone/drive-upload", json={"job_ids": [job], "login_ids": [first, 999]}
    )
    assert r.status_code == 400  # one not hers: nothing queued
    r = ann.post("/clone/drive-upload", json={"job_ids": [job], "login_ids": [first]})
    # The suite runs the upload job inline: it is done by the answer.
    assert [u["status"] for u in r.json()["uploads"]] == ["done"]


def test_queueing_returns_only_the_users_own_runs(drive, tmp_path):
    """Another user's run id in the request: its upload rows (folder,
    Drive link, error) are not in the answer."""
    ann = _client("ann@example.com")
    bob = _client("bob@example.com")
    ann_login = _account_folder(ann)
    bob_login = _account_folder(bob, "bob.ig")
    ann_job = _finished_run("ann@example.com", tmp_path, "a.mp4")
    bob_job = _finished_run("bob@example.com", tmp_path, "b.mp4")
    bob.post(
        "/clone/drive-upload", json={"job_ids": [bob_job], "login_ids": [bob_login]}
    )

    r = ann.post(
        "/clone/drive-upload",
        json={"job_ids": [ann_job, bob_job], "login_ids": [ann_login]},
    )

    assert r.status_code == 200, r.text
    assert [u["job_id"] for u in r.json()["uploads"]] == [ann_job]


def test_an_upload_that_cannot_be_queued_fails_and_can_be_retried(
    drive, tmp_path, monkeypatch
):
    ann = _client("ann@example.com")
    login = _account_folder(ann)
    job = _finished_run("ann@example.com", tmp_path)
    working = drive_uploads.enqueue

    def redis_down(*_args, **_kwargs):
        msg = "redis down"
        raise ConnectionError(msg)

    monkeypatch.setattr(drive_uploads, "enqueue", redis_down)
    ann.post("/clone/drive-upload", json={"job_ids": [job], "login_ids": [login]})
    [state] = ann.get("/clone/drive-uploads", params={"job": job}).json()
    assert (state["status"], state["error"]) == (
        "failed",
        "Could not queue the upload: redis down",
    )

    monkeypatch.setattr(drive_uploads, "enqueue", working)
    ann.post("/clone/drive-upload", json={"job_ids": [job], "login_ids": [login]})
    [state] = ann.get("/clone/drive-uploads", params={"job": job}).json()
    assert state["status"] == "done"


def test_a_stranded_queued_upload_is_queued_again_once_stale(
    drive, tmp_path, monkeypatch
):
    """The worker died mid-batch (deploy): the row stays `queued`. Upload
    leaves it while it may still run, and queues it again once stale."""
    monkeypatch.setenv("OFM_GDRIVE_UPLOAD_STALE_S", "60")
    ann = _client("ann@example.com")
    login = _account_folder(ann)
    job = _finished_run("ann@example.com", tmp_path)
    working = drive_uploads.enqueue
    monkeypatch.setattr(drive_uploads, "enqueue", lambda *_a, **_k: None)
    ann.post("/clone/drive-upload", json={"job_ids": [job], "login_ids": [login]})
    monkeypatch.setattr(drive_uploads, "enqueue", working)

    ann.post("/clone/drive-upload", json={"job_ids": [job], "login_ids": [login]})
    [state] = ann.get("/clone/drive-uploads", params={"job": job}).json()
    assert state["status"] == "queued"  # not stale yet: never doubled
    assert _files_in(drive, "ann.ig - Anna") == []

    with time_machine.travel(time.time() + 120):
        ann.post("/clone/drive-upload", json={"job_ids": [job], "login_ids": [login]})
        [state] = ann.get("/clone/drive-uploads", params={"job": job}).json()
    assert state["status"] == "done"
    assert len(_files_in(drive, "ann.ig - Anna")) == 1


def test_any_error_uploading_a_file_fails_that_upload(drive, tmp_path, monkeypatch):
    """Not only Drive errors: a file removed after the check, a bug -- the
    row says why instead of staying `queued`."""
    ann = _client("ann@example.com")
    login = _account_folder(ann)
    job = _finished_run("ann@example.com", tmp_path)

    def vanished(*_args, **_kwargs):
        msg = "clone.mp4 is gone"
        raise FileNotFoundError(msg)

    monkeypatch.setattr(drive, "upload", vanished)
    r = ann.post("/clone/drive-upload", json={"job_ids": [job], "login_ids": [login]})

    assert r.status_code == 200, r.text
    [state] = ann.get("/clone/drive-uploads", params={"job": job}).json()
    assert (state["status"], state["error"]) == (
        "failed",
        "Upload failed: clone.mp4 is gone",
    )


def test_dev_never_uploads_into_a_live_root_kept_from_a_prod_copy(drive, tmp_path):
    """A dev database copied from prod, before any sweep: the stored root is
    the live "ALL ACCOUNTS" and the account folder sits in it. The worker
    checks the root by name and refuses; nothing is written to Drive."""
    ann = _client("ann@example.com")
    login = _account_folder(ann)
    job = _finished_run("ann@example.com", tmp_path)
    ann_id = users.by_email("ann@example.com").id
    live = drive.seed_folder(
        "ALL ACCOUNTS", "mydrive-ann@example.com", owner="ann@example.com"
    )
    live_account = drive.seed_folder("ann.ig - Anna", live, owner="ann@example.com")
    folder_store.save_account_folder(login, live_account, "ann.ig - Anna", live)
    users.set_drive_root(ann_id, live)
    drive.clear_calls()

    r = ann.post("/clone/drive-upload", json={"job_ids": [job], "login_ids": [login]})

    assert r.status_code == 200, r.text
    [state] = ann.get("/clone/drive-uploads", params={"job": job}).json()
    assert (state["status"], state["error"]) == (
        "failed",
        (
            "The account's folder is not in its owner's DEV - ALL ACCOUNTS folder:"
            " Sync on /drive, then upload again"
        ),
    )
    assert drive.names_in(live_account) == []
    assert drive.writes == []


def test_clone_and_drive_offer_connecting_google_drive_until_it_is(drive):
    """Sign-in asks no Drive access: /clone and /drive link straight to the
    one consent that does, and stop once it is given."""
    connect = "/auth/google/start?link=1&amp;next="
    ann = _client("ann@example.com", google=False)
    assert f"{connect}/clone" in ann.get("/clone").text
    assert f"{connect}/drive" in ann.get("/drive/live").text

    connected = _client("bob@example.com")
    assert connect not in connected.get("/clone").text
    assert connect not in connected.get("/drive/live").text
