"""
Upload filenames are attacker-controlled: the multipart part's `filename` is
whatever the client typed, so "../../cookies/cookies.txt" is a legal value
and joining it onto an upload directory writes outside that directory. Every
path built from an upload name goes through task_helpers.safe_filename.
"""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ["APP_PASSWORD_VA"] = "test-va"
os.environ.setdefault("SESSION_SECRET", "test-secret")
os.environ.setdefault("DISCORD_WEBHOOK_URL", "https://discord.example/webhooks/test")
os.environ.setdefault("APP_BASE_URL", "https://test.example")

import io
from unittest import mock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from ofmhelpers.web.main import app
from ofmhelpers.web.routers.task_helpers import safe_filename
from ofmhelpers.web.routers.workflow import todo as todo_router
from ofmhelpers.web.stores import todos


@pytest.mark.parametrize(
    ("sent", "expected"),
    [
        ("clip.mp4", "clip.mp4"),
        ("../../cookies/cookies.txt", "cookies.txt"),
        ("../../../etc/passwd", "passwd"),
        ("/absolute/path/clip.mp4", "clip.mp4"),
        (r"..\..\windows\style.txt", "style.txt"),
        ("weird name (1).png", "weird name (1).png"),
    ],
)
def test_safe_filename_keeps_only_the_basename(sent, expected):
    assert safe_filename(sent) == expected


@pytest.mark.parametrize("sent", ["", None, "..", ".", "../", "/"])
def test_safe_filename_rejects_names_with_no_basename(sent):
    with pytest.raises(HTTPException) as exc:
        safe_filename(sent)
    assert exc.value.status_code == 400


@pytest.fixture
def va_client():
    c = TestClient(app)
    c.post("/login", data={"password": "test-va", "next": "/"})
    return c


def test_todo_asset_upload_cannot_escape_its_directory(
    va_client, monkeypatch, tmp_path
):
    """The end-to-end version: a VA (the lowest-privileged role, and the one
    allowed to upload assets) sends a traversing filename and the bytes still
    land inside the todo's own asset directory."""
    asset_root = tmp_path / "todo_assets"
    monkeypatch.setattr(todo_router, "ASSET_ROOT", asset_root)
    monkeypatch.setattr(todo_router, "send_webhook", mock.Mock())

    todo = todos.add_todo("Model", "https://example.com/reel", "", "admin")
    outside = tmp_path / "pwned.mp4"

    r = va_client.post(
        f"/todo/{todo['id']}/asset",
        files={"file": ("../../pwned.mp4", io.BytesIO(b"payload"), "video/mp4")},
        follow_redirects=False,
    )

    assert r.status_code == 303
    assert not outside.exists()
    written = list(asset_root.rglob("*"))
    assert [p.name for p in written if p.is_file()] == ["pwned.mp4"]
    assert (asset_root / todo["id"] / "pwned.mp4").read_bytes() == b"payload"
    # The display name stored on the todo is the sanitized one, not the
    # traversing string the client sent.
    assert todos.get_todo(todo["id"])["asset_name"] == "pwned.mp4"
