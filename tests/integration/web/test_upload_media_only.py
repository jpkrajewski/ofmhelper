import io

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from signin import sign_in

from ofmhelpers.media.metadata_cleaner import SUPPORTED_EXTENSIONS
from ofmhelpers.web.main import app
from ofmhelpers.web.routers.task_helpers import (
    IMAGE_KINDS,
    MEDIA_KINDS,
    VIDEO_KINDS,
    classify_kind,
    media_response,
    require_upload_kind,
)

IMAGE_AND_VIDEO = IMAGE_KINDS | VIDEO_KINDS


@pytest.fixture
def va_client():
    c = TestClient(app)
    sign_in(c)
    return c


@pytest.mark.parametrize("name", ["clip.mp4", "shot.PNG", "photo.jpeg", "reel.mov"])
def test_media_names_pass(name):
    assert require_upload_kind(name, IMAGE_AND_VIDEO) == name


@pytest.mark.parametrize(
    "name",
    ["payload.html", "payload.svg", "payload.xhtml", "notes.txt", "book.xlsx", "x.mp3"],
)
def test_non_image_video_names_are_rejected(name):
    with pytest.raises(HTTPException) as exc:
        require_upload_kind(name, IMAGE_AND_VIDEO)
    assert exc.value.status_code == 400


def test_reference_store_also_takes_audio():
    assert require_upload_kind("voice.mp3", MEDIA_KINDS) == "voice.mp3"


def test_media_response_serves_images_inline_with_nosniff(tmp_path):
    path = tmp_path / "shot.png"
    path.write_bytes(b"not really a png")

    r = media_response(path)

    assert r.media_type == "image/png"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "attachment" not in r.headers.get("content-disposition", "")


def test_media_response_forces_a_download_for_anything_else(tmp_path):
    path = tmp_path / "legacy.html"
    path.write_bytes(b"<script>steal()</script>")

    r = media_response(path)

    assert r.media_type == "application/octet-stream"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "attachment" in r.headers["content-disposition"]


def test_clean_images_rejects_a_non_image_upload(va_client):
    r = va_client.post(
        "/clean-images/run",
        files={
            "files": ("payload.html", io.BytesIO(b"<script>x</script>"), "image/png")
        },
    )

    assert r.status_code == 400


def test_clean_images_accepts_every_extension_the_cleaner_supports(va_client, tmp_path):
    for ext in SUPPORTED_EXTENSIONS:
        assert classify_kind(f"x{ext}") in IMAGE_KINDS, ext
