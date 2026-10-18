"""/drive: "Upload to Drive" on a /clone batch -- connect through Google's
consent page on the first click, upload the batch's finished videos after."""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ["APP_PASSWORD_VA"] = "test-va"
os.environ.setdefault("SESSION_SECRET", "test-secret")

from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials

from ofmhelpers.gdrive import UploadReport
from ofmhelpers.web.main import app
from ofmhelpers.web.routers import drive
from ofmhelpers.web.schemas import ResultFile
from ofmhelpers.web.stores.drive_connections import get_connection
from ofmhelpers.web.stores.jobs import create_job, run_job

FOLDER = "https://drive.google.com/drive/folders/f1"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "secret")
    c = TestClient(app, follow_redirects=False)
    c.post("/login", data={"password": "test-va", "next": "/"})
    return c


def _clone_params() -> dict:
    return {
        "clone_batch": "b1",
        "clone_index": 0,
        "prompt": "p",
        "resolution": "720P",
        "audio": False,
        "duration": 5,
        "reference_images": ["face.png"],
        "reference_videos": ["src.mp4"],
    }


def _clone_job(path: Path, actor: str = "va") -> str:
    path.write_bytes(b"video")  # a missing result file is healed away
    job_id = create_job("clone", _clone_params(), actor=actor)
    result = [ResultFile(name=path.name, path=str(path))]
    run_job(job_id, lambda: result, {})
    return job_id


def _connect(client, monkeypatch, next_url="/clone?drive_batch=b1") -> None:
    """Out to Google and back, as the browser would go."""
    creds = Credentials(token="at", refresh_token="rt")
    monkeypatch.setattr(drive, "exchange_code", lambda _code, _pending: creds)
    monkeypatch.setattr(drive, "account_email", lambda _creds: "me@gmail.com")
    response = client.get("/drive/connect", params={"next": next_url})
    url = urlparse(response.headers["location"])
    query = parse_qs(url.query)
    assert url.netloc == "accounts.google.com"
    assert query["scope"] == ["https://www.googleapis.com/auth/drive.file"]
    assert query["access_type"] == ["offline"]
    assert query["prompt"] == ["consent"]
    assert query["code_challenge_method"] == ["S256"]
    back = client.get(
        "/drive/callback", params={"state": query["state"][0], "code": "c"}
    )
    assert back.headers["location"] == next_url


def test_page_has_the_button_next_to_recreate_batch(client, tmp_path):
    _clone_job(tmp_path / "v.mp4")
    html = client.get("/clone").text
    assert "↻ Recreate batch</button>" in html
    assert 'class="btn-outline batch-drive-btn">⬆ Upload to Drive</button>' in html


def test_first_click_asks_to_connect_and_comes_back_to_the_batch(client, tmp_path):
    job_id = _clone_job(tmp_path / "v.mp4")

    response = client.post(
        "/drive/upload", data={"job_ids": [job_id], "next": "/clone?drive_batch=b1"}
    )

    assert response.status_code == 409
    assert response.json()["connect_url"] == (
        "/drive/connect?next=%2Fclone%3Fdrive_batch%3Db1"
    )


def test_connected_click_uploads_the_batch(client, tmp_path, monkeypatch):
    video = tmp_path / "v.mp4"
    job_id = _clone_job(video)
    _connect(client, monkeypatch)
    calls = []

    def fake_upload(token, paths):
        calls.append((token, paths))
        return UploadReport(folder_url=FOLDER, uploaded=[p.name for p in paths])

    monkeypatch.setattr(drive, "upload_to_drive", fake_upload)

    queued = client.post("/drive/upload", data={"job_ids": [job_id]}).json()
    status = client.get(f"/drive/uploads/{queued['job_id']}").json()

    assert queued["drive"] == "me@gmail.com"
    assert calls == [("rt", [video])]
    assert status == {"status": "done", "error": None, "folder_url": FOLDER}


def test_partial_upload_fails_the_job_saying_how_many_went_up(
    client, tmp_path, monkeypatch
):
    ids = [_clone_job(tmp_path / f"{n}.mp4") for n in "abc"]
    _connect(client, monkeypatch)
    report = UploadReport(
        folder_url=FOLDER, uploaded=["a.mp4"], already_there=["b.mp4"], failed=["c.mp4"]
    )
    monkeypatch.setattr(drive, "upload_to_drive", lambda _t, _p: report)

    queued = client.post("/drive/upload", data={"job_ids": ids}).json()
    status = client.get(f"/drive/uploads/{queued['job_id']}").json()

    assert status["status"] == "failed"
    assert status["error"] == (
        "2/3 in Drive; failed: c.mp4. Click Upload again to retry the rest."
    )


def test_logout_deletes_the_drive_so_the_next_person_connects_their_own(
    client, tmp_path, monkeypatch
):
    job_id = _clone_job(tmp_path / "v.mp4")
    _connect(client, monkeypatch)

    client.post("/logout")
    client.post("/login", data={"password": "test-va", "next": "/"})

    response = client.post("/drive/upload", data={"job_ids": [job_id]})
    assert response.status_code == 409


def test_a_job_the_session_cannot_see_uploads_nothing(client, tmp_path, monkeypatch):
    admin_job = _clone_job(tmp_path / "v.mp4", actor="admin")
    _connect(client, monkeypatch)

    response = client.post("/drive/upload", data={"job_ids": [admin_job]})

    assert response.status_code == 400


def test_revoked_login_disconnects_so_the_next_click_reconnects(
    client, tmp_path, monkeypatch
):
    job_id = _clone_job(tmp_path / "v.mp4")
    _connect(client, monkeypatch)

    def revoked(*_a):
        msg = "invalid_grant"
        raise RefreshError(msg)

    monkeypatch.setattr(drive, "upload_to_drive", revoked)

    queued = client.post("/drive/upload", data={"job_ids": [job_id]}).json()
    status = client.get(f"/drive/uploads/{queued['job_id']}").json()

    assert status["status"] == "failed"
    assert "reconnect" in status["error"]
    again = client.post("/drive/upload", data={"job_ids": [job_id]})
    assert again.status_code == 409


def test_callback_with_a_foreign_state_is_refused(client):
    client.get("/drive/connect")
    assert client.get("/drive/callback?state=forged&code=c").status_code == 400


def test_cancel_on_google_goes_back_unconnected(client, tmp_path):
    job_id = _clone_job(tmp_path / "v.mp4")
    out = urlparse(client.get("/drive/connect?next=/clone").headers["location"])
    state = parse_qs(out.query)["state"][0]

    back = client.get(f"/drive/callback?state={state}&error=access_denied")

    assert back.headers["location"] == "/clone"
    assert client.post("/drive/upload", data={"job_ids": [job_id]}).status_code == 409


@pytest.mark.parametrize(
    ("next_url", "expected"),
    [("/clone", "/clone"), ("//evil.example", "/"), ("https://evil.example", "/")],
)
def test_next_is_never_an_open_redirect(next_url, expected):
    assert drive._local(next_url) == expected


def test_unconfigured_says_so(client, monkeypatch):
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID")
    response = client.post("/drive/upload", data={"job_ids": ["x"]})
    assert response.status_code == 503
    assert get_connection("x") is None


def test_a_batch_with_nothing_finished_has_no_button(client):
    job_id = create_job("clone", _clone_params(), actor="va")
    run_job(job_id, _fail, {})
    assert "batch-drive-btn" not in client.get("/clone").text


def _fail():
    msg = "Credits insufficient"
    raise RuntimeError(msg)
