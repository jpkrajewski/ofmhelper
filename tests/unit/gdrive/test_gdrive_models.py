import pytest

from ofmhelpers.db.drive_folders.schema import (
    FOLDER_MIME,
    DriveFile,
)
from ofmhelpers.integrations.storage import (
    DriveListing,
    FolderItems,
)


@pytest.mark.parametrize(
    ("name", "mime", "kind"),
    [
        ("clip.mp4", "video/mp4", "video"),
        ("photo.jpg", "image/jpeg", "image"),
        ("folder", FOLDER_MIME, "folder"),
        ("notes.pdf", "application/pdf", "file"),
        ("reel.mp4", "application/octet-stream", "video"),
        ("reel.mov", "application/octet-stream", "video"),
        ("pic.png", "application/octet-stream", "image"),
        ("whatever.bin", "application/octet-stream", "file"),
    ],
)
def test_kind_falls_back_to_the_name_when_the_mime_is_generic(name, mime, kind):
    assert DriveFile(id="1", name=name, mimeType=mime).kind == kind


def test_a_listing_gives_each_folder_its_items_and_none_for_one_not_listed():
    clip = DriveFile(id="1", name="clip.mp4", mimeType="video/mp4")
    listing = DriveListing(
        folders=[
            FolderItems(folder_id="a", items=[clip]),
            FolderItems(folder_id="b"),
        ]
    )

    assert listing.of("a") == [clip]
    assert listing.of("b") == []
    assert listing.of("never asked") == []
