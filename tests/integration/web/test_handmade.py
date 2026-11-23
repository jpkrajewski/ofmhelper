# Copyright (C) 2026 jpkrajewski

import pytest
from fastapi.testclient import TestClient
from signin import sign_in

from ofmhelpers.db.jobs.store import get_job
from ofmhelpers.db.users import store as users
from ofmhelpers.db.users.schema import Role
from ofmhelpers.db.users.store import _repository as user_repository
from ofmhelpers.features.storage import jobs as storage_jobs
from ofmhelpers.integrations.storage.gdrive.fake import FakeDrive, fake_drive
from ofmhelpers.web.main import app

OWNER = "ann@example.com"
VA = "noelyn@example.com"
ROOT = "handmade-root-folder"
PHONE = {
    "label": "Phone 01",
    "model_id": "iphone-13",
    "color_id": "blue",
    "owner": "Anna",
}


@pytest.fixture
def drive(fake_externals, monkeypatch, tmp_path) -> FakeDrive:
    monkeypatch.setenv("OFM_GDRIVE_SERVICE_ACCOUNT", "{}")
    monkeypatch.setenv("OFM_GDRIVE_ROOT_FOLDER", ROOT)
    monkeypatch.setenv("OFM_UPLOADS_ROOT", str(tmp_path / "uploads"))
    return fake_drive()


def _owner() -> TestClient:
    c = TestClient(app)
    user = sign_in(c, OWNER)
    user_repository().signed_in(user.id, "", f"fake-refresh:{OWNER}")
    phone = c.post("/phones/add", json=PHONE).json()["id"]
    c.login_id = c.post(  # type: ignore[attr-defined]
        f"/phones/{phone}/logins",
        json={"service": "instagram", "username": "ann.ig"},
    ).json()["id"]
    storage_jobs.sync()
    return c


def _va() -> TestClient:
    c = TestClient(app)
    va = sign_in(c, VA, Role.VA)
    owner = users.by_email(OWNER)
    assert owner is not None
    users.set_owner(va.id, owner.id)
    return c


def _reels(*names: str) -> list[tuple[str, tuple[str, bytes, str]]]:
    return [("files", (name, f"video {name}".encode(), "video/mp4")) for name in names]


def _drive_files(drive: FakeDrive) -> list[str]:
    owner = users.by_email(OWNER)
    assert owner is not None
    assert owner.drive_root_id
    (folder,) = drive.named("ann.ig - Anna", owner.drive_root_id)
    return [n for n in drive.names_in(folder) if n != "POSTED"]


def _send(client: TestClient, job_id: str, login: int):
    return client.post(
        "/clone/drive-upload",
        json={"job_ids": [job_id], "login_ids": [login]},
    )


def test_a_vas_reels_stay_on_the_server_one_card_each(drive):
    owner = _owner()
    va = _va()

    r = va.post("/handmade/run", files=_reels("a.mp4", "b.mov"))

    assert r.status_code == 202, r.text
    ids = r.json()["job_ids"]
    assert len(ids) == 2
    for job_id in ids:
        job = get_job(job_id)
        assert job is not None
        assert (job.status, job.error, len(job.result)) == ("done", None, 1)
    assert _drive_files(drive) == []
    page = va.get("/handmade").text
    assert page.count('data-task="handmade_upload"') == 2
    assert "Upload to Drive" not in page
    assert "drive.google.com" not in page
    assert owner.get("/handmade").text.count("Done by VA: ") == 2


def test_the_owner_picks_the_folder_a_vas_reel_goes_to(drive):
    owner = _owner()
    va = _va()
    job_id = va.post("/handmade/run", files=_reels("a.mp4")).json()["job_id"]

    page = owner.get("/handmade").text
    picker = owner.get("/drive", params={"upload": job_id}).text
    r = _send(owner, job_id, owner.login_id)  # type: ignore[attr-defined]

    assert "Upload to Drive" in page
    assert f'data-job="{job_id}"' in picker
    assert f'data-back="/handmade#job-{job_id}"' in picker
    assert r.status_code == 200, r.text
    assert len(_drive_files(drive)) == 1
    assert "In Drive: <a" in owner.get("/handmade").text
    assert "In Drive: ann.ig - Anna" in va.get("/handmade").text
    assert _send(va, job_id, owner.login_id).status_code == 403  # type: ignore[attr-defined]


def test_the_owner_takes_a_vas_reel_back_out_of_drive(drive):
    owner = _owner()
    va = _va()
    job_id = va.post("/handmade/run", files=_reels("a.mp4")).json()["job_id"]
    (upload,) = _send(owner, job_id, owner.login_id).json()["uploads"]  # type: ignore[attr-defined]

    r = owner.post(f"/clone/drive-uploads/{upload['id']}/remove")

    assert r.status_code == 200, r.text
    assert _drive_files(drive) == []


@pytest.mark.parametrize(
    ("files", "why"),
    [
        ([], "Pick at least one video"),
        (_reels("a.mp4", "b.mp4", "c.mp4"), "At most 2 videos at once"),
        ([("files", ("notes.txt", b"x", "text/plain"))], None),
    ],
)
def test_what_is_not_a_short_batch_of_videos_is_refused(drive, monkeypatch, files, why):
    monkeypatch.setenv("OFM_HANDMADE_MAX_FILES", "2")
    _owner()
    va = _va()

    r = va.post("/handmade/run", files=files) if files else va.post("/handmade/run")

    assert r.status_code == 400
    if why is not None:
        assert r.json()["detail"] == why


def test_only_a_va_uploads_here(drive):
    owner = _owner()

    page = owner.get("/handmade").text
    r = owner.post("/handmade/run", files=_reels("a.mp4"))

    assert 'id="handmade-form"' not in page
    assert r.status_code == 403


def test_the_owner_can_send_a_reel_into_the_vas_own_folder(drive):
    owner = _owner()
    va = _va()
    storage_jobs.sync()
    job_id = va.post("/handmade/run", files=_reels("a.mp4")).json()["job_id"]
    va_user = users.by_email(VA)
    assert va_user is not None

    picker = owner.get("/drive", params={"upload": job_id}).text
    r = owner.post(
        "/clone/drive-upload",
        json={"job_ids": [job_id], "va_ids": [va_user.id]},
    )
    again = owner.post(
        "/clone/drive-upload",
        json={"job_ids": [job_id], "va_ids": [va_user.id]},
    )

    assert f'data-va-folder="{va_user.id}"' in picker
    assert r.status_code == 200, r.text
    (upload,) = r.json()["uploads"]
    assert (upload["va_id"], upload["login_id"], upload["status"]) == (
        va_user.id,
        None,
        "done",
    )
    owner_user = users.by_email(OWNER)
    assert owner_user is not None
    (folder,) = drive.named(VA, owner_user.drive_root_id or "")
    assert len(drive.names_in(folder)) == 1
    assert again.json()["queued"] == 0
    assert "In Drive: <a" in owner.get("/handmade").text
    assert (
        owner.post("/clone/drive-upload", json={"job_ids": [job_id]}).status_code == 400
    )
