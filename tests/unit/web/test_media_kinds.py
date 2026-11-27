# Copyright (C) 2026 jpkrajewski

import pytest

from ofmhelpers.features.kinds import FileKind
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
