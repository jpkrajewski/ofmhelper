# Copyright (C) 2026 jpkrajewski

import secrets
import uuid
lazy from collections.abc import Iterator

import pytest

from ofmhelpers.config import settings
from ofmhelpers.integrations.storage import (
    DriveError,
    FolderGoneError,
    StorageClient,
)
from ofmhelpers.integrations.storage.gdrive import connect
from ofmhelpers.integrations.storage.gdrive.fake import FakeDrive

FAKE_ROOT = "contract-root"


def _missing_id() -> str:
    return "1" + secrets.token_urlsafe(24)[:32]


@pytest.fixture
def drive(backend, monkeypatch) -> StorageClient:
    if backend == "fake":
        fake = FakeDrive()
        fake.seed_folder("ALL ACCOUNTS", item_id=FAKE_ROOT, shared=True)
        return fake
    monkeypatch.setenv("OFM_DRIVE_READ_LIVE", "0")
    return connect()


@pytest.fixture
def parent(drive, backend) -> Iterator[str]:
    root = FAKE_ROOT if backend == "fake" else settings.gdrive.root_folder_id
    assert root, "set OFM_GDRIVE_ROOT_FOLDER to a sandbox folder in .env"
    return drive.create_folder(root, f"contract-{uuid.uuid4().hex[:8]}").id


def test_names_are_not_unique_and_find_returns_the_oldest(drive, parent):
    first = drive.create_folder(parent, "same name")
    second = drive.create_folder(parent, "same name")
    assert first.id != second.id
    found = drive.find_folder(parent, "same name")
    assert found is not None
    assert found.id == first.id


def test_children_are_newest_first(drive, parent):
    older = drive.create_folder(parent, "older")
    newer = drive.create_folder(parent, "newer")
    assert [f.id for f in drive.children(parent)] == [newer.id, older.id]
    assert [f.id for f in drive.children(parent, folders_only=True)] == [
        newer.id,
        older.id,
    ]


def test_children_of_lists_several_folders_at_once(drive, parent):
    one = drive.create_folder(parent, "one")
    two = drive.create_folder(parent, "two")
    empty = drive.create_folder(parent, "empty")
    inner_old = drive.create_folder(one.id, "inner old")
    inner_new = drive.create_folder(one.id, "inner new")
    other = drive.create_folder(two.id, "other")

    found = drive.children_of([one.id, two.id, empty.id])

    assert [f.id for f in found.of(one.id)] == [inner_new.id, inner_old.id]
    assert [f.id for f in found.of(two.id)] == [other.id]
    assert found.of(empty.id) == []
    assert [f.folder_id for f in found.folders] == [one.id, two.id, empty.id]
    assert drive.children_of([]).folders == []


def test_find_folder_is_none_for_a_name_not_there(drive, parent):
    assert drive.find_folder(parent, "never made") is None


def test_ensure_folder_reuses_what_is_there(drive, parent):
    made = drive.ensure_folder(parent, "once")
    assert drive.ensure_folder(parent, "once").id == made.id
    assert [f.name for f in drive.children(parent)] == ["once"]


def test_rename_keeps_the_id(drive, parent):
    made = drive.create_folder(parent, "before")
    drive.rename(made.id, "after")
    assert drive.folder(made.id).name == "after"


def test_a_file_knows_its_parent_and_a_folder_has_no_preview(drive, parent):
    made = drive.create_folder(parent, "child")
    item = drive.file(made.id)
    assert item.parents == [parent]
    assert item.is_folder
    assert drive.thumbnail(item, 200) is None


def test_a_folder_knows_its_parent(drive, parent):
    made = drive.create_folder(parent, "POSTED")
    assert drive.folder(made.id).parents == [parent]


def _never_existed(call) -> FolderGoneError:
    for _ in range(5):
        try:
            call(_missing_id())
        except FolderGoneError as gone:
            return gone
        except DriveError as exc:
            if "HttpError 400" not in str(exc):
                raise
    msg = "five made-up ids in a row answered 400"
    raise AssertionError(msg)


def test_a_missing_folder_is_gone(drive, parent):
    assert isinstance(_never_existed(drive.folder), FolderGoneError)


def test_listing_a_missing_folder_shows_nothing(drive, parent):
    listed: list = []
    for _ in range(5):
        try:
            listed = drive.children(_missing_id())
            break
        except FolderGoneError:
            break
        except DriveError as exc:
            if "HttpError 400" not in str(exc):
                raise
    else:
        pytest.fail("Drive called 5 made-up ids malformed in a row")
    assert listed == []


def _trash(drive, backend, folder_id: str) -> None:
    if backend == "fake":
        drive.trash(folder_id)
    else:
        drive._files.update(
            fileId=folder_id,
            body={"trashed": True},
            supportsAllDrives=True,
        ).execute()


def test_a_trashed_folder_with_contents_is_gone_as_a_folder(drive, backend, parent):
    trashed = drive.create_folder(parent, "trashed")
    drive.create_folder(trashed.id, "child")
    _trash(drive, backend, trashed.id)

    with pytest.raises(FolderGoneError):
        drive.folder(trashed.id)


def test_trash_file_takes_an_item_out_of_its_folder(drive, parent):
    child = drive.create_folder(parent, "to-trash")
    drive.trash_file(child.id)
    assert child.id not in [c.id for c in drive.children(parent)]
    with pytest.raises(FolderGoneError):
        drive.folder(child.id)
    assert isinstance(_never_existed(drive.trash_file), FolderGoneError)


def test_find_file_is_none_for_a_name_not_there_and_never_a_folder(drive, parent):
    assert drive.find_file(parent, "content_links") is None
    drive.create_folder(parent, "content_links")
    assert drive.find_file(parent, "content_links") is None


def test_reading_what_is_no_google_sheet_as_a_sheet_is_refused(drive, parent):
    folder = drive.create_folder(parent, "not a sheet")
    with pytest.raises(DriveError):
        drive.sheet(folder.id)
