# Copyright (C) 2026 jpkrajewski

import json

import pytest
from pydantic import BaseModel

from ofmhelpers.features.assets import manifests
from ofmhelpers.features.assets.models import ImageUpload
from ofmhelpers.features.kinds import FileKind
from ofmhelpers.features.pictures import service
from ofmhelpers.features.refusals import RefusedError
from ofmhelpers.integrations.downloaders.generic import VIDEO_EXTENSIONS


@pytest.mark.parametrize("ext", sorted(VIDEO_EXTENSIONS))
def test_every_video_a_download_keeps_is_a_video(ext: str) -> None:
    assert FileKind.from_ext(f"reel{ext}") == FileKind.VIDEO
    assert FileKind.from_ext(f"REEL{ext.upper()}") == FileKind.VIDEO


def test_file_kind_owns_its_extensions() -> None:
    assert ".png" in FileKind.IMAGE.extensions
    assert ".mp4" in FileKind.VIDEO.extensions
    assert ".mp3" in FileKind.AUDIO.extensions
    assert not FileKind.OTHER.extensions


def test_file_kind_classifies_an_unknown_extension_as_other() -> None:
    assert FileKind.from_ext("page.html") == FileKind.OTHER


class PictureSubmission(BaseModel):
    provider: str = "kie"
    api_key: str = "fake"
    preset: int = 1
    pictures: list[ImageUpload] = []
    pictures_manifest: str


def test_pictures_refuses_a_stored_video(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(manifests.ref_usage, "record_use", lambda user_id, path: None)
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"video")
    form = PictureSubmission(
        pictures_manifest=json.dumps(
            [{"kind": "existing", "path": str(video)}],
        ),
    )

    with pytest.raises(RefusedError, match="Pictures only"):
        service._pictures(form, owner_id=1, assets_root=tmp_path)
