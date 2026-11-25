# Copyright (C) 2026 jpkrajewski

import io

import pillow_heif
import pytest
from fastapi import UploadFile
from PIL import Image

from ofmhelpers.features.assets.models import validate_image_upload
from ofmhelpers.features.refusals import AssetRefusedError
from ofmhelpers.web.routers.task_helpers import save_asset


def heic_upload(name: str, size=(64, 32)) -> UploadFile:
    buf = io.BytesIO()
    pillow_heif.from_pillow(Image.new("RGB", size, (200, 10, 10))).save(buf)
    buf.seek(0)
    return UploadFile(file=buf, filename=name, size=buf.getbuffer().nbytes)


@pytest.mark.parametrize("name", ["broken.heic", "broken.heif"])
def test_corrupt_heic_is_refused(name):
    upload = UploadFile(file=io.BytesIO(b"not a heic"), filename=name)

    with pytest.raises(AssetRefusedError, match="Unreadable HEIC image"):
        validate_image_upload(upload)


def test_save_asset_stores_heic_as_jpeg(tmp_path):
    upload = validate_image_upload(heic_upload("IMG_0001.HEIC"))
    path = save_asset(upload, tmp_path)

    assert path.endswith("__IMG_0001.jpg")
    with Image.open(path) as img:
        assert img.format == "JPEG"
