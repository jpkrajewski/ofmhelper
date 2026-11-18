"""An upload belongs to who made it: after an admin moves a VA to another
employer, the new employer neither lists nor trashes the old employer's
files in the old employer's own Google Drive (audit finding
`drive-uploads:remove-authorized-by-job-reader-not-uploader`)."""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ.setdefault("SESSION_SECRET", "test-secret")

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from signin import sign_in

from ofmhelpers.db.stores import users
from ofmhelpers.db.stores.jobs import create_job
from ofmhelpers.db.stores.users import _repository as user_repository
from ofmhelpers.features.drive import folders as drive_folders
from ofmhelpers.features.jobs.runner import run_job
from ofmhelpers.integrations.gdrive.fake import fake_drive
from ofmhelpers.schemas import Role
from ofmhelpers.web.main import app

PHONE = {
    "label": "Phone 01",
    "model_id": "iphone-13",
    "color_id": "blue",
    "owner": "Anna",
}
X, Y, VA = "xavier@example.com", "yara@example.com", "va@example.com"


@pytest.fixture
def drive(fake_externals, monkeypatch, tmp_path):
    monkeypatch.setenv("OFM_GDRIVE_SERVICE_ACCOUNT", "{}")
    monkeypatch.setenv("OFM_GDRIVE_ROOT_FOLDER", "uploader-root-folder")
    monkeypatch.setenv("OFM_UPLOADS_ROOT", str(tmp_path / "uploads"))
    return fake_drive()


def _client(email: str) -> TestClient:
    c = TestClient(app)
    user = sign_in(c, email)
    user_repository().signed_in(user.id, "", f"fake-refresh:{email}")
    return c


def _account_folder(client: TestClient) -> int:
    phone = client.post("/phones/add", json=PHONE).json()["id"]
    login = client.post(
        f"/phones/{phone}/logins", json={"service": "instagram", "username": "x.ig"}
    ).json()["id"]
    drive_folders.sync()
    return login


def _vas_run(tmp_path: Path) -> str:
    video = tmp_path / "va.mp4"
    video.write_bytes(b"\x00\x00\x00\x18ftypmp42 fake")
    params = {
        "prompt": "p",
        "resolution": "720P",
        "audio": False,
        "duration": 5,
        "reference_images": [],
        "reference_videos": [str(video)],
        "clone_batch": "batch-va",
        "clone_index": 0,
    }
    job_id = create_job("clone", params, actor=str(users.by_email(VA).id))
    run_job(job_id, lambda: [{"name": video.name, "path": str(video)}], {})
    return job_id


def _uploaded_then_moved(drive, tmp_path):
    """X's upload of their VA's run, done; the VA now works for Y."""
    x, y = _client(X), _client(Y)
    login = _account_folder(x)
    sign_in(TestClient(app), VA, Role.VA)
    users.set_owner(users.by_email(VA).id, users.by_email(X).id)
    job = _vas_run(tmp_path)
    x.post("/clone/drive-upload", json={"job_ids": [job], "login_ids": [login]})
    [state] = x.get("/clone/drive-uploads", params={"job": job}).json()
    assert state["status"] == "done"
    users.set_owner(users.by_email(VA).id, users.by_email(Y).id)
    return x, y, job, state


def _in_x_folder(drive) -> list[str]:
    """The files in X's account folder (not the POSTED folder)."""
    root = users.by_email(X).drive_root_id
    (folder,) = drive.named("x.ig - Anna", root)
    return [n for n in drive.names_in(folder) if n != "POSTED"]


def test_the_new_employer_cannot_remove_the_old_employers_upload(drive, tmp_path):
    _x, y, _job, state = _uploaded_then_moved(drive, tmp_path)
    assert len(_in_x_folder(drive)) == 1

    r = y.post(f"/clone/drive-uploads/{state['id']}/remove")

    assert r.status_code == 400
    assert r.json()["detail"] == "No such upload"
    assert len(_in_x_folder(drive)) == 1  # X's file is still in X's folder


def test_the_new_employer_lists_none_of_the_old_employers_uploads(drive, tmp_path):
    _x, y, job, _state = _uploaded_then_moved(drive, tmp_path)

    assert y.get("/clone/drive-uploads", params={"job": job}).json() == []


def test_the_owner_lists_and_removes_the_upload_they_made_of_their_vas_run(
    drive, tmp_path
):
    x = _client(X)
    login = _account_folder(x)
    sign_in(TestClient(app), VA, Role.VA)
    users.set_owner(users.by_email(VA).id, users.by_email(X).id)
    job = _vas_run(tmp_path)
    x.post("/clone/drive-upload", json={"job_ids": [job], "login_ids": [login]})

    [state] = x.get("/clone/drive-uploads", params={"job": job}).json()
    assert state["status"] == "done"
    assert len(_in_x_folder(drive)) == 1

    r = x.post(f"/clone/drive-uploads/{state['id']}/remove")

    assert r.status_code == 200, r.text
    assert r.json()["status"] == "removed"
    assert _in_x_folder(drive) == []
