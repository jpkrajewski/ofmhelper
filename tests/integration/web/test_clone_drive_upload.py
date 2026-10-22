"""/clone "Upload to Drive": a finished run's video goes into an Instagram
account's Drive folder as the clicker's Google account -- once per folder,
only into folders they may use, only their own runs, and a failure is
shown and may be retried."""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ.setdefault("SESSION_SECRET", "test-secret")

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from signin import sign_in

from ofmhelpers.gdrive import DriveError
from ofmhelpers.gdrive.fake import fake_drive
from ofmhelpers.web import drive_folders, drive_uploads
from ofmhelpers.web.main import app
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

    r = ann.post("/clone/drive-upload", json={"job_ids": [job], "login_id": login})

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

    again = ann.post("/clone/drive-upload", json={"job_ids": [job], "login_id": login})
    assert again.json()["queued"] == 0
    assert len(_files_in(drive, "ann.ig - Anna")) == 1  # never twice
    assert "In Drive:" in ann.get("/clone").text


def test_without_google_drive_connected_nothing_is_queued(drive, tmp_path):
    ann = _client("ann@example.com", google=False)
    login = _account_folder(ann)
    job = _finished_run("ann@example.com", tmp_path)
    r = ann.post("/clone/drive-upload", json={"job_ids": [job], "login_id": login})
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
        "/clone/drive-upload", json={"job_ids": [bob_job], "login_id": ann_login}
    )
    assert r.status_code == 400
    r = ann.post(
        "/clone/drive-upload", json={"job_ids": [bob_job], "login_id": ann_login}
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
    ann.post("/clone/drive-upload", json={"job_ids": [job], "login_id": login})
    [state] = ann.get("/clone/drive-uploads", params={"job": job}).json()
    assert (state["status"], state["error"]) == (
        "failed",
        "Uploading: Drive refused (quota)",
    )
    assert "Drive upload to ann.ig - Anna failed" in ann.get("/clone").text

    monkeypatch.setattr(drive_uploads, "connect_as", working)
    retry = ann.post("/clone/drive-upload", json={"job_ids": [job], "login_id": login})
    assert retry.status_code == 200
    [state] = ann.get("/clone/drive-uploads", params={"job": job}).json()
    assert state["status"] == "done"


def test_the_batch_header_offers_only_the_users_folders(drive, tmp_path):
    ann = _client("ann@example.com")
    bob = _client("bob@example.com")
    login = _account_folder(ann)
    _finished_run("ann@example.com", tmp_path, "a.mp4")
    _finished_run("bob@example.com", tmp_path, "b.mp4")

    assert f'<option value="{login}">ann.ig - Anna</option>' in ann.get("/clone").text
    bob_page = bob.get("/clone").text
    assert "ann.ig - Anna" not in bob_page
    assert "drive-target" not in bob_page  # Bob has no account folder
