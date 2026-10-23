"""gdrive/models.py DriveFile.kind: what icon /drive draws for an item."""

import pytest

from ofmhelpers.gdrive.models import FOLDER_MIME, DriveFile


@pytest.mark.parametrize(
    ("name", "mime", "kind"),
    [
        ("clip.mp4", "video/mp4", "video"),
        ("photo.jpg", "image/jpeg", "image"),
        ("folder", FOLDER_MIME, "folder"),
        ("notes.pdf", "application/pdf", "file"),
        # Drive reports a phone upload as a generic type: the name decides.
        ("reel.mp4", "application/octet-stream", "video"),
        ("reel.mov", "application/octet-stream", "video"),
        ("pic.png", "application/octet-stream", "image"),
        ("whatever.bin", "application/octet-stream", "file"),
    ],
)
def test_kind_falls_back_to_the_name_when_the_mime_is_generic(name, mime, kind):
    assert DriveFile(id="1", name=name, mimeType=mime).kind == kind
