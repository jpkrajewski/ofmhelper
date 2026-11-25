# Copyright (C) 2026 jpkrajewski

import asyncio
import io
from tempfile import SpooledTemporaryFile

import pytest
from fastapi import HTTPException, UploadFile
from fastapi.testclient import TestClient
from signin import sign_in

from ofmhelpers.config import settings
from ofmhelpers.features.assets.models import validate_file_upload
from ofmhelpers.features.kinds import FileKind
from ofmhelpers.features.refusals import AssetRefusedError
from ofmhelpers.media.metadata_cleaner import SUPPORTED_EXTENSIONS
from ofmhelpers.web.main import app
from ofmhelpers.web.routers.task_helpers import media_response, require_upload_kind

IMAGE_AND_VIDEO = frozenset({FileKind.IMAGE, FileKind.VIDEO})


@pytest.fixture
def va_client():
    c = TestClient(app)
    sign_in(c)
    return c


def test_any_upload_has_a_sanitized_filename():
    upload = UploadFile(file=io.BytesIO(b"one"), filename="../a.txt", size=3)

    validated = validate_file_upload(upload)

    assert validated.filename == "a.txt"
    assert validated.kind == FileKind.OTHER


def test_upload_size_is_measured_without_changing_the_stream_position():
    stream = io.BytesIO(b"one")
    stream.seek(1)
    upload = UploadFile(file=stream, filename="a.txt", size=999)

    validated = validate_file_upload(upload)

    assert validated.size == 3
    assert stream.tell() == 1


def test_empty_upload_is_rejected():
    upload = UploadFile(file=io.BytesIO(), filename="a.txt")

    with pytest.raises(AssetRefusedError, match="File is empty"):
        validate_file_upload(upload)


def test_upload_over_the_configured_limit_is_rejected(monkeypatch):
    monkeypatch.setattr(settings.web, "max_upload_mb", 1)
    upload = UploadFile(
        file=io.BytesIO(b"x" * (1024 * 1024 + 1)),
        filename="a.txt",
    )

    with pytest.raises(AssetRefusedError, match="1 MB"):
        validate_file_upload(upload)


def test_validated_upload_supports_inherited_writes():
    with SpooledTemporaryFile(max_size=1024) as stream:
        stream.write(b"one")
        stream.seek(0)
        upload = UploadFile(file=stream, filename="a.txt")

        validated = validate_file_upload(upload)
        asyncio.run(validated.write(b"!"))

        stream.seek(0)
        assert stream.read() == b"!ne"


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
    assert (
        require_upload_kind(
            "voice.mp3",
            frozenset({FileKind.IMAGE, FileKind.VIDEO, FileKind.AUDIO}),
        )
        == "voice.mp3"
    )


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
            "files": ("payload.html", io.BytesIO(b"<script>x</script>"), "image/png"),
        },
    )

    assert r.status_code == 422


def test_clean_images_accepts_every_extension_the_cleaner_supports(va_client, tmp_path):
    for ext in SUPPORTED_EXTENSIONS:
        assert FileKind.from_ext(f"x{ext}") == FileKind.IMAGE, ext
