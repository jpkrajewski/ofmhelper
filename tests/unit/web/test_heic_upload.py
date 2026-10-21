"""iPhone HEIC/HEIF uploads are converted to a max-quality JPEG on the way in,
so every tool downstream only ever sees a format it can read."""

import io

import pillow_heif
import pytest
from fastapi import HTTPException, UploadFile
from PIL import Image

from ofmhelpers.web.routers.task_helpers import convert_heic_upload, save_asset


def heic_upload(name: str, size=(64, 32)) -> UploadFile:
    buf = io.BytesIO()
    pillow_heif.from_pillow(Image.new("RGB", size, (200, 10, 10))).save(buf)
    buf.seek(0)
    return UploadFile(file=buf, filename=name)


@pytest.mark.parametrize("name", ["IMG_0001.HEIC", "photo.heif"])
def test_heic_upload_becomes_full_size_jpeg(name):
    converted = convert_heic_upload(heic_upload(name))

    assert converted.filename == name.rsplit(".", 1)[0] + ".jpg"
    with Image.open(converted.file) as img:
        assert img.format == "JPEG"
        assert img.size == (64, 32)


def test_non_heic_upload_is_untouched():
    upload = UploadFile(file=io.BytesIO(b"png bytes"), filename="ref.png")
    assert convert_heic_upload(upload) is upload


def test_corrupt_heic_is_a_400():
    upload = UploadFile(file=io.BytesIO(b"not a heic"), filename="broken.heic")
    with pytest.raises(HTTPException) as exc:
        convert_heic_upload(upload)
    assert exc.value.status_code == 400


def test_save_asset_stores_heic_as_jpeg(tmp_path):
    path = save_asset(heic_upload("IMG_0001.HEIC"), tmp_path)

    assert path.endswith("__IMG_0001.jpg")
    with Image.open(path) as img:
        assert img.format == "JPEG"
