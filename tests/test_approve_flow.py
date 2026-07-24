"""
Covers the public (no-login) Discord magic-link approval flow:
web/approval_tokens.py + web/routers/approve.py, wired in from
web/routers/todo.py's upload_asset. The whole point is a reviewer can tap
the link from Discord on their phone with **no session cookie** and have it
approve the asset and kick off the Google Drive upload in one shot.
"""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ["APP_PASSWORD_VA"] = "test-va"
os.environ.setdefault("SESSION_SECRET", "test-secret")
os.environ.setdefault("DISCORD_WEBHOOK_URL", "https://discord.example/webhooks/test")
os.environ.setdefault("APP_BASE_URL", "https://test.example")

import unittest.mock as mock

import pytest
from fastapi.testclient import TestClient

from ofmhelpers.web.main import app
from ofmhelpers.web import todos, approval_tokens
from ofmhelpers.web.routers import todo as todo_router


@pytest.fixture
def client():
    c = TestClient(app)
    c.post("/login", data={"password": "test-admin", "next": "/"})
    return c


@pytest.fixture
def anon_client():
    """No login at all -- this is the client shape a phone tapping a
    Discord link actually is."""
    return TestClient(app)


@pytest.fixture(autouse=True)
def _isolated_store(monkeypatch, tmp_path):
    monkeypatch.setattr(todos, "STORE_FILE", tmp_path / "todos.json")
    monkeypatch.setattr(todo_router, "ASSET_ROOT", tmp_path / "todo_assets")
    monkeypatch.setattr(
        approval_tokens, "STORE_FILE", tmp_path / "approval_tokens.json"
    )
    monkeypatch.setattr(todo_router, "send_webhook", mock.Mock())


def _upload_asset(client, todo_id, filename="ready.png", content=b"fake bytes"):
    files = {"file": (filename, content, "image/png")}
    client.post(f"/todo/{todo_id}/asset", files=files)


def _approve_url_for(todo_id):
    """Pulls the approve link straight out of the mocked Discord call --
    exactly what a reviewer would tap from the real message. The link is
    hidden behind masked markdown text in the embed description
    ("[label](url)"), never shown as a raw URL, so it has to be parsed out
    rather than read off a dedicated field."""
    content, embeds = todo_router.send_webhook.call_args[0]
    description = embeds[0]["description"]
    return description.split("](", 1)[1].rstrip(")")


def test_anonymous_tap_approves_and_starts_drive_upload(client, anon_client):
    todo = todos.add_todo("Model A", "https://a", "", "admin")
    _upload_asset(client, todo["id"])
    approve_url = _approve_url_for(todo["id"])
    path = approve_url.replace("https://test.example", "")

    with mock.patch.object(
        todo_router, "gdrive_upload_file", return_value="drive-file-123"
    ) as upload_mock:
        r = anon_client.get(path, follow_redirects=False)

    assert r.status_code == 303
    assert r.headers["location"] == "/approve/result?status=ok"
    upload_mock.assert_called_once()

    stored = todos.get_todo(todo["id"])
    assert stored["approved"] is True
    assert stored["drive_file_id"] == "drive-file-123"


def test_anonymous_can_view_asset_preview_with_no_session(client, anon_client):
    todo = todos.add_todo("Model A", "https://a", "", "admin")
    _upload_asset(client, todo["id"], content=b"the actual bytes")
    approve_url = _approve_url_for(todo["id"])
    asset_path = approve_url.replace("https://test.example", "") + "/asset"

    r = anon_client.get(asset_path)
    assert r.status_code == 200
    assert r.content == b"the actual bytes"


def test_asset_preview_page_carries_og_video_tags_pointing_at_the_asset(
    client, anon_client
):
    """The /asset/preview HTML wrapper (see routers/approve.py's
    asset_preview) is what Discord's crawler is pointed at for video
    assets instead of the raw file -- it needs Open Graph video tags
    pointing back at the real /asset URL for Discord to build a playable
    embed from."""
    todo = todos.add_todo("Model A", "https://a", "", "admin")
    files = {"file": ("clip.mp4", b"fake video bytes", "video/mp4")}
    client.post(f"/todo/{todo['id']}/asset", files=files)

    approve_url = _approve_url_for(todo["id"])
    token_path = approve_url.replace("https://test.example", "")
    video_url = f"https://test.example{token_path}/asset"

    r = anon_client.get(f"{token_path}/asset/preview")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert f'content="{video_url}"' in r.text
    assert 'property="og:video"' in r.text
    assert 'property="twitter:player"' in r.text


def test_asset_preview_404s_for_unknown_token(anon_client):
    r = anon_client.get("/approve/not-a-real-token/asset/preview")
    assert r.status_code == 404


def test_reusing_the_same_link_fails_the_second_time(client, anon_client):
    todo = todos.add_todo("Model A", "https://a", "", "admin")
    _upload_asset(client, todo["id"])
    approve_url = _approve_url_for(todo["id"])
    path = approve_url.replace("https://test.example", "")

    with mock.patch.object(todo_router, "gdrive_upload_file", return_value="id-1"):
        anon_client.get(path, follow_redirects=False)

    with mock.patch.object(
        todo_router, "gdrive_upload_file", return_value="id-2"
    ) as upload_mock:
        r = anon_client.get(path, follow_redirects=False)

    assert r.status_code == 303
    assert "status=error" in r.headers["location"]
    assert "reason=used" in r.headers["location"]
    upload_mock.assert_not_called()
    assert todos.get_todo(todo["id"])["drive_file_id"] == "id-1"


def test_link_for_a_since_replaced_asset_is_rejected_as_stale(client, anon_client):
    todo = todos.add_todo("Model A", "https://a", "", "admin")
    _upload_asset(client, todo["id"], filename="v1.png")
    stale_url = _approve_url_for(todo["id"])
    stale_path = stale_url.replace("https://test.example", "")

    # VA replaces the asset before the reviewer gets to the old link.
    _upload_asset(client, todo["id"], filename="v2.png")

    r = anon_client.get(stale_path, follow_redirects=False)
    assert r.status_code == 303
    assert "reason=stale" in r.headers["location"]
    assert todos.get_todo(todo["id"])["approved"] is False


def test_expired_link_is_rejected(client, anon_client, monkeypatch):
    todo = todos.add_todo("Model A", "https://a", "", "admin")
    _upload_asset(client, todo["id"])
    approve_url = _approve_url_for(todo["id"])
    path = approve_url.replace("https://test.example", "")

    future = approval_tokens.time.time() + approval_tokens.TOKEN_TTL_SECONDS + 10
    monkeypatch.setattr(approval_tokens.time, "time", lambda: future)

    r = anon_client.get(path, follow_redirects=False)
    assert r.status_code == 303
    assert "reason=expired" in r.headers["location"]
    assert todos.get_todo(todo["id"])["approved"] is False


def test_unknown_token_is_rejected(anon_client):
    r = anon_client.get("/approve/not-a-real-token", follow_redirects=False)
    assert r.status_code == 303
    assert "reason=not_found" in r.headers["location"]


def test_result_page_renders_success_and_failure(anon_client):
    ok = anon_client.get("/approve/result?status=ok")
    assert ok.status_code == 200
    assert "Approved" in ok.text

    failed = anon_client.get("/approve/result?status=error&reason=used")
    assert failed.status_code == 200
    assert "already been used" in failed.text
