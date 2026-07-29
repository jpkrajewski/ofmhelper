"""
Uploads are restricted to media, and what comes back out can't run as a page
in our own origin.

The attack this closes: a VA uploads "payload.html" as a todo asset, the
admin opens it from the Todo page, and it executes same-origin with the
admin's session cookie. Two independent brakes -- the upload is rejected by
extension (require_upload_kind), and anything non-media already on disk is
served as an octet-stream attachment with nosniff (media_response).
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
from ofmhelpers.web.routers.task_helpers import (
    IMAGE_VIDEO_KINDS,
    MEDIA_KINDS,
    media_response,
    require_upload_kind,
)
from ofmhelpers.web.routers.workflow import todo as todo_router
from ofmhelpers.web.stores import todos


@pytest.fixture
def va_client():
    c = TestClient(app)
    c.post("/login", data={"password": "test-va", "next": "/"})
    return c


@pytest.fixture(autouse=True)
def _isolated_assets(monkeypatch, tmp_path):
    monkeypatch.setattr(todo_router, "ASSET_ROOT", tmp_path / "todo_assets")
    monkeypatch.setattr(todo_router, "send_webhook", mock.Mock())


@pytest.mark.parametrize("name", ["clip.mp4", "shot.PNG", "photo.jpeg", "reel.mov"])
def test_media_names_pass(name):
    assert require_upload_kind(name, IMAGE_VIDEO_KINDS) == name


@pytest.mark.parametrize(
    "name",
    ["payload.html", "payload.svg", "payload.xhtml", "notes.txt", "book.xlsx", "x.mp3"],
)
def test_non_image_video_names_are_rejected(name):
    with pytest.raises(HTTPException) as exc:
        require_upload_kind(name, IMAGE_VIDEO_KINDS)
    assert exc.value.status_code == 400


def test_reference_store_also_takes_audio():
    """The generation tools' reference store is the one place audio is a
    legitimate input -- it must not be narrowed to image/video."""
    assert require_upload_kind("voice.mp3", MEDIA_KINDS) == "voice.mp3"


def test_todo_rejects_an_html_upload(va_client):
    todo = todos.add_todo("Model", "https://example.com/reel", "", "admin")

    r = va_client.post(
        f"/todo/{todo['id']}/asset",
        files={
            "file": ("payload.html", io.BytesIO(b"<script>x</script>"), "video/mp4")
        },
        follow_redirects=False,
    )

    # Declared Content-Type says video/mp4 -- the extension is what decides.
    assert r.status_code == 400
    assert todos.get_todo(todo["id"])["asset_path"] is None


def test_media_response_serves_images_inline_with_nosniff(tmp_path):
    path = tmp_path / "shot.png"
    path.write_bytes(b"not really a png")

    r = media_response(path)

    assert r.media_type == "image/png"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "attachment" not in r.headers.get("content-disposition", "")


def test_media_response_forces_a_download_for_anything_else(tmp_path):
    """Covers assets stored before the upload allowlist existed."""
    path = tmp_path / "legacy.html"
    path.write_bytes(b"<script>steal()</script>")

    r = media_response(path)

    assert r.media_type == "application/octet-stream"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "attachment" in r.headers["content-disposition"]


def test_stored_html_asset_is_not_served_as_html(va_client, tmp_path):
    """End-to-end: a .html asset that predates the allowlist still can't come
    back as an executable same-origin page."""
    todo = todos.add_todo("Model", "https://example.com/reel", "", "admin")
    legacy = tmp_path / "legacy.html"
    legacy.write_bytes(b"<script>steal()</script>")
    todos.attach_asset(todo["id"], str(legacy), "legacy.html")

    r = va_client.get(f"/todo/{todo['id']}/asset")

    assert r.status_code == 200
    assert r.headers["content-type"] == "application/octet-stream"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "attachment" in r.headers["content-disposition"]


def test_clean_images_rejects_a_non_image_upload(va_client):
    """clean_metadata *skips* an extension it doesn't know instead of failing,
    so an unprocessed .html used to survive in the job dir and come back from
    /clean-images/files/... as a page on our own origin."""
    r = va_client.post(
        "/clean-images/run",
        files={
            "files": ("payload.html", io.BytesIO(b"<script>x</script>"), "image/png")
        },
    )

    assert r.status_code == 400


def test_clean_images_accepts_every_extension_the_cleaner_supports(va_client, tmp_path):
    """The allowlist must not be narrower than utils/metadata_cleaner's."""
    from ofmhelpers.utils.metadata_cleaner import SUPPORTED_EXTENSIONS
    from ofmhelpers.web.routers.task_helpers import IMAGE_KINDS, classify_kind

    for ext in SUPPORTED_EXTENSIONS:
        assert classify_kind(f"x{ext}") in IMAGE_KINDS, ext
