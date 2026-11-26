# Copyright (C) 2026 jpkrajewski

import asyncio
import io
from tempfile import SpooledTemporaryFile

import pytest
from fastapi import HTTPException, UploadFile
from fastapi.testclient import TestClient
from PIL import GifImagePlugin, Image
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


def test_truncated_jpeg_scan_is_rejected():
    encoded = io.BytesIO()
    Image.new("RGB", (64, 64), color="red").save(encoded, "JPEG")
    data = encoded.getvalue()
    scan = data.index(b"\xff\xda")
    header_length = int.from_bytes(data[scan + 2 : scan + 4], "big")
    truncated = data[: scan + 2 + header_length + 1]
    Image.open(io.BytesIO(truncated)).verify()
    upload = UploadFile(file=io.BytesIO(truncated), filename="broken.jpg")

    with pytest.raises(AssetRefusedError, match="Unreadable image"):
        validate_file_upload(upload)


def test_corrupt_later_gif_frame_is_rejected():
    encoded = io.BytesIO()
    frames = [
        Image.new("RGB", (64, 64), color="red"),
        Image.new("RGB", (64, 64), color="blue"),
    ]
    frames[0].save(
        encoded,
        "GIF",
        save_all=True,
        append_images=frames[1:],
        duration=100,
        loop=0,
    )
    data = encoded.getvalue()
    truncated = data[:-11] + data[-1:]
    with Image.open(io.BytesIO(truncated)) as image:
        image.load()
    upload = UploadFile(file=io.BytesIO(truncated), filename="broken.gif")

    with pytest.raises(AssetRefusedError, match="Unreadable image"):
        validate_file_upload(upload)


def test_gif_without_end_trailer_is_rejected():
    encoded = io.BytesIO()
    frames = [
        Image.new("RGB", (64, 64), color="red"),
        Image.new("RGB", (64, 64), color="blue"),
    ]
    frames[0].save(
        encoded,
        "GIF",
        save_all=True,
        append_images=frames[1:],
        duration=100,
        loop=0,
    )
    truncated = encoded.getvalue()[:-1]
    with Image.open(io.BytesIO(truncated)) as image:
        image.load()
    upload = UploadFile(file=io.BytesIO(truncated), filename="broken.gif")

    with pytest.raises(AssetRefusedError, match="Unreadable image"):
        validate_file_upload(upload)


def test_corrupt_later_mpo_frame_is_rejected():
    encoded = io.BytesIO()
    frames = [
        Image.new("RGB", (64, 64), color="red"),
        Image.new("RGB", (64, 64), color="blue"),
    ]
    frames[0].save(encoded, "MPO", save_all=True, append_images=frames[1:])
    truncated = encoded.getvalue()[:-20]
    with Image.open(io.BytesIO(truncated)) as image:
        image.load()
    upload = UploadFile(file=io.BytesIO(truncated), filename="broken.jpg")

    with pytest.raises(AssetRefusedError, match="Unreadable image"):
        validate_file_upload(upload)


def test_animation_over_the_frame_limit_is_rejected(monkeypatch):
    monkeypatch.setattr(settings.web, "max_image_frames", 1, raising=False)
    encoded = io.BytesIO()
    frames = [
        Image.new("RGB", (64, 64), color="red"),
        Image.new("RGB", (64, 64), color="blue"),
    ]
    frames[0].save(
        encoded,
        "GIF",
        save_all=True,
        append_images=frames[1:],
        duration=100,
        loop=0,
    )
    upload = UploadFile(file=io.BytesIO(encoded.getvalue()), filename="animation.gif")

    with pytest.raises(AssetRefusedError, match="Too many image frames"):
        validate_file_upload(upload)


def test_animation_over_the_total_pixel_limit_is_rejected(monkeypatch):
    monkeypatch.setattr(settings.web, "max_image_total_pixels", 64 * 64, raising=False)
    encoded = io.BytesIO()
    frames = [
        Image.new("RGB", (64, 64), color="red"),
        Image.new("RGB", (64, 64), color="blue"),
    ]
    frames[0].save(
        encoded,
        "GIF",
        save_all=True,
        append_images=frames[1:],
        duration=100,
        loop=0,
    )
    upload = UploadFile(file=io.BytesIO(encoded.getvalue()), filename="animation.gif")

    with pytest.raises(AssetRefusedError, match="Image exceeds the pixel limit"):
        validate_file_upload(upload)


def test_gif_validation_does_not_discover_all_frames(monkeypatch):
    def unexpected_frame_discovery(self):
        msg = "GIF frame discovery was not bounded"
        raise AssertionError(msg)

    monkeypatch.setattr(
        GifImagePlugin.GifImageFile,
        "n_frames",
        property(unexpected_frame_discovery),
    )
    encoded = io.BytesIO()
    Image.new("RGB", (64, 64), color="red").save(encoded, "GIF")
    upload = UploadFile(file=io.BytesIO(encoded.getvalue()), filename="valid.gif")

    validated = validate_file_upload(upload)

    assert validated.filename == "valid.gif"


def test_truncated_apng_is_refused():
    encoded = io.BytesIO()
    frames = [
        Image.new("RGB", (64, 64), color="red"),
        Image.new("RGB", (64, 64), color="blue"),
    ]
    frames[0].save(encoded, "PNG", save_all=True, append_images=frames[1:])
    truncated = encoded.getvalue()[:-20]
    upload = UploadFile(file=io.BytesIO(truncated), filename="broken.png")

    with pytest.raises(AssetRefusedError, match="Unreadable image"):
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
