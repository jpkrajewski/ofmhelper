"""Google Drive: what /drive relies on, on `FakeDrive` and on the real
Drive. The real half works only in a sandbox folder (OFM_GDRIVE_ROOT_FOLDER
in the dev .env; `drive_folders.root_folder` refuses the production one),
inside a fresh "contract-<id>" folder per test; nothing is deleted."""

import uuid
from collections.abc import Iterator

import pytest

from ofmhelpers.gdrive import DriveApi, FolderGoneError, connect
from ofmhelpers.gdrive.fake import FakeDrive
from ofmhelpers.web import drive_folders

FAKE_ROOT = "contract-root"


@pytest.fixture
def drive(backend, monkeypatch) -> DriveApi:
    if backend == "fake":
        fake = FakeDrive()
        fake.seed_folder("ALL ACCOUNTS", item_id=FAKE_ROOT, shared=True)
        return fake
    return connect()


@pytest.fixture
def parent(drive, backend) -> Iterator[str]:
    root = FAKE_ROOT if backend == "fake" else drive_folders.root_folder()
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


def test_a_missing_folder_is_gone(drive, parent):
    with pytest.raises(FolderGoneError):
        drive.folder("1" + uuid.uuid4().hex * 2)


def test_listing_a_missing_folder_is_gone(drive, parent):
    with pytest.raises(FolderGoneError):
        drive.children("1" + uuid.uuid4().hex * 2)


def _trash(drive, backend, folder_id: str) -> None:
    """What the VA does in the Drive UI (not part of DriveApi: the app
    never deletes)."""
    if backend == "fake":
        drive.trash(folder_id)
    else:
        drive._files.update(
            fileId=folder_id, body={"trashed": True}, supportsAllDrives=True
        ).execute()


def test_a_trashed_folder_still_lists_but_is_gone_as_a_folder(drive, backend, parent):
    """Why /drive checks `folder` before listing: the listing alone never
    says the folder was deleted."""
    trashed = drive.create_folder(parent, "trashed")
    child = drive.create_folder(trashed.id, "child")
    _trash(drive, backend, trashed.id)

    assert [f.id for f in drive.children(trashed.id)] == [child.id]
    with pytest.raises(FolderGoneError):
        drive.folder(trashed.id)
