"""/drive: connect a Google Drive through Google's consent page; /clone
uploads each finished video into it."""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ["APP_PASSWORD_VA"] = "test-va"
os.environ.setdefault("SESSION_SECRET", "test-secret")

from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from google.oauth2.credentials import Credentials
from googleapiclient.errors import HttpError
from httplib2 import Response

from ofmhelpers.web.main import app
from ofmhelpers.web.routers import drive
from ofmhelpers.web.routers.generation import clone
from ofmhelpers.web.schemas import ResultFile
from ofmhelpers.web.stores.drive_connections import add_connection, get_connection


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "secret")
    c = TestClient(app, follow_redirects=False)
    c.post("/login", data={"password": "test-va", "next": "/"})
    return c


def _connect(client) -> str:
    """Goes out to Google; answers the state Google would echo back."""
    response = client.get("/drive/connect?next=/clone")
    assert response.status_code == 303
    url = urlparse(response.headers["location"])
    query = parse_qs(url.query)
    assert url.netloc == "accounts.google.com"
    assert query["scope"] == ["https://www.googleapis.com/auth/drive.file"]
    assert query["access_type"] == ["offline"]
    assert query["prompt"] == ["consent"]
    assert query["code_challenge_method"] == ["S256"]
    return query["state"][0]


def _google_answers(monkeypatch, refresh_token="rt"):
    creds = Credentials(token="at", refresh_token=refresh_token)
    monkeypatch.setattr(drive, "exchange_code", lambda _code, _pending: creds)
    monkeypatch.setattr(drive, "account_email", lambda _creds: "me@gmail.com")


def test_connect_then_callback_saves_the_drive(client, monkeypatch):
    _google_answers(monkeypatch)
    state = _connect(client)

    response = client.get(f"/drive/callback?state={state}&code=c")

    assert response.headers["location"] == "/clone"
    assert "Saving to me@gmail.com" in client.get("/clone").text


def test_callback_with_a_foreign_state_is_refused(client, monkeypatch):
    _google_answers(monkeypatch)
    _connect(client)

    assert client.get("/drive/callback?state=forged&code=c").status_code == 400


def test_cancel_on_google_goes_back_unconnected(client):
    state = _connect(client)

    response = client.get(f"/drive/callback?state={state}&error=access_denied")

    assert response.headers["location"] == "/clone"
    assert "Connect Google Drive" in client.get("/clone").text


def test_disconnect_forgets_the_drive(client, monkeypatch):
    _google_answers(monkeypatch)
    client.get(f"/drive/callback?state={_connect(client)}&code=c")

    client.post("/drive/disconnect", data={"next": "/clone"})

    assert "Connect Google Drive" in client.get("/clone").text


@pytest.mark.parametrize(
    ("next_url", "expected"),
    [("/clone", "/clone"), ("//evil.example", "/"), ("https://evil.example", "/")],
)
def test_next_is_never_an_open_redirect(next_url, expected):
    assert drive._local(next_url) == expected


def test_unconfigured_disables_the_button(client, monkeypatch):
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID")
    html = client.get("/clone").text
    assert (
        '<button type="button" class="btn-outline" disabled>Connect Google Drive'
        in html
    )
    assert 'href="/drive/connect' not in html
    assert client.get("/drive/connect").status_code == 503


def test_clone_uploads_the_result_to_the_connected_drive(monkeypatch):
    connection = add_connection("me@gmail.com", "rt")
    result = [ResultFile(name="v.mp4", path="/x/v.mp4", remote_url="https://r/v")]
    monkeypatch.setattr(clone, "_run_wan3", lambda **_k: result)
    uploads = []

    def fake_upload(token, path):
        uploads.append((token, path))
        return "https://drive.google.com/file/d/1/view"

    monkeypatch.setattr(clone, "upload_file", fake_upload)

    [out] = clone._run_clone("b", 0, drive_connection=connection, assets_root="/x")

    assert uploads == [("rt", Path("/x/v.mp4"))]
    assert out.drive_url == "https://drive.google.com/file/d/1/view"
    assert out.to_result()["drive_url"] == out.drive_url


def test_failed_upload_keeps_the_result(monkeypatch):
    connection = add_connection("me@gmail.com", "rt")
    result = [ResultFile(name="v.mp4", path="/x/v.mp4", remote_url="https://r/v")]
    monkeypatch.setattr(clone, "_run_wan3", lambda **_k: result)

    def broken(*_a):
        raise HttpError(Response({"status": "403"}), b"quota")

    monkeypatch.setattr(clone, "upload_file", broken)

    assert (
        clone._run_clone("b", 0, drive_connection=connection, assets_root="/x")
        == result
    )
    assert get_connection(connection) is not None
