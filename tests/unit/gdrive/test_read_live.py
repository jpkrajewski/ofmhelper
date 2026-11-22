from pathlib import Path

import pytest

from ofmhelpers.features.storage.client import read_live
from ofmhelpers.features.storage.models import StorageAccess
from ofmhelpers.features.storage.service import StorageService
from ofmhelpers.integrations.storage import (
    DriveError,
    DriveFolder,
    ReadOnlyDrive,
    ScopedDrive,
)
from ofmhelpers.integrations.storage.gdrive.fake import FakeDrive, FakePersonDrive


class _Reads:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def _rec(self, name, result=None):
        self.calls.append(name)
        return result

    def folder(self, folder_id):
        return self._rec("folder", DriveFolder(id=folder_id, name="ALL ACCOUNTS"))

    def children(self, *args, **kwargs):
        return self._rec("children", [])

    def children_of(self, *args, **kwargs):
        return self._rec("children_of", {})

    def find_folder(self, *args, **kwargs):
        return self._rec("find_folder")

    def file(self, *args, **kwargs):
        return self._rec("file")

    def thumbnail(self, *args, **kwargs):
        return self._rec("thumbnail")


def test_read_only_drive_passes_every_read_through():
    inner = _Reads()
    ro = ReadOnlyDrive(inner)

    ro.folder("root")
    ro.children("root", folders_only=True)
    ro.children_of(["a"])
    ro.find_folder("root", "x")
    ro.file("f")
    ro.thumbnail(None, 220)

    assert inner.calls == [
        "folder",
        "children",
        "children_of",
        "find_folder",
        "file",
        "thumbnail",
    ]


def test_read_only_drive_blocks_every_write():
    ro = ReadOnlyDrive(_Reads())

    with pytest.raises(DriveError):
        ro.create_folder("root", "name")
    with pytest.raises(DriveError):
        ro.ensure_folder("root", "name")
    with pytest.raises(DriveError):
        ro.rename("id", "name")
    with pytest.raises(DriveError):
        ro.upload("root", Path("x.mp4"), "x.mp4", "video/mp4")
    with pytest.raises(DriveError):
        ro.grant("id", "a@b.com")
    with pytest.raises(DriveError):
        ro.trash_file("id")


def test_read_live_mode_is_off_in_production(monkeypatch):
    monkeypatch.setenv("OFM_DRIVE_READ_LIVE", "1")
    monkeypatch.setenv("OFM_ENV", "production")
    monkeypatch.delenv("OFM_FAKE_EXTERNALS", raising=False)
    assert read_live() is False


def test_read_live_mode_is_on_in_dev_when_set(monkeypatch):
    monkeypatch.setenv("OFM_DRIVE_READ_LIVE", "1")
    monkeypatch.setenv("OFM_ENV", "development")
    assert read_live() is True


def test_read_live_mode_is_off_by_default(monkeypatch):
    monkeypatch.delenv("OFM_DRIVE_READ_LIVE", raising=False)
    monkeypatch.setenv("OFM_ENV", "development")
    assert read_live() is False


def _service(raw: _Reads) -> StorageService:
    access = StorageAccess(owner_id=1, client=raw, read_live=read_live())
    return StorageService.compose(access)


def test_live_mode_hands_the_services_a_read_only_drive_and_review_the_raw_one(
    monkeypatch,
):
    monkeypatch.setenv("OFM_DRIVE_READ_LIVE", "1")
    monkeypatch.setenv("OFM_ENV", "development")
    raw = _Reads()
    service = _service(raw)
    for part in (
        service.folders,
        service.va_folders,
        service.uploads,
        service.space,
        service.bin,
    ):
        assert isinstance(part.client, ReadOnlyDrive)
    assert service.review.client is raw

    monkeypatch.setenv("OFM_DRIVE_READ_LIVE", "0")
    plain = _service(raw)
    for part in (
        plain.folders,
        plain.va_folders,
        plain.uploads,
        plain.space,
        plain.bin,
        plain.review,
    ):
        assert part.client is raw


def _folders(monkeypatch):
    return _service(_Reads()).folders


def test_live_mode_reads_the_live_root_name_and_accepts_it(monkeypatch):
    monkeypatch.setenv("OFM_DRIVE_READ_LIVE", "1")
    monkeypatch.setenv("OFM_ENV", "development")
    folders = _folders(monkeypatch)
    assert folders.root_name() == "ALL ACCOUNTS"
    assert folders._is_root(DriveFolder(id="x", name="ALL ACCOUNTS")) is True
    assert folders._is_root(DriveFolder(id="x", name="something else")) is True


def test_dev_without_live_mode_keeps_the_sandbox_root(monkeypatch):
    monkeypatch.delenv("OFM_DRIVE_READ_LIVE", raising=False)
    monkeypatch.setenv("OFM_ENV", "development")
    folders = _folders(monkeypatch)
    assert folders.root_name() == "DEV - ALL ACCOUNTS"
    assert folders._is_root(DriveFolder(id="x", name="ALL ACCOUNTS")) is False


def _scoped(tmp_path: Path) -> tuple[FakeDrive, FakePersonDrive, str, Path]:
    fake = FakeDrive()
    person = FakePersonDrive(fake, "ann@example.com")
    root = person.create_folder("root", "DEV - ALL ACCOUNTS").id
    video = tmp_path / "a.mp4"
    video.write_bytes(b"mp4")
    return fake, person, root, video


def test_scoped_drive_writes_into_folders_it_makes_finds_or_reads_in_its_root(
    tmp_path,
):
    fake, person, root, video = _scoped(tmp_path)
    scoped = ScopedDrive(person, root)
    made = scoped.create_folder(root, "GOOD REELS")
    by_hand = fake.seed_folder("TRIAL REELS", parent=root)
    found = scoped.find_folder(root, "TRIAL REELS")
    assert found is not None
    later = ScopedDrive(person, root)
    remembered = later.folder(made.id)

    scoped.upload(made.id, video, "a.mp4", "video/mp4")
    scoped.upload(by_hand, video, "b.mp4", "video/mp4")
    later.upload(remembered.id, video, "c.mp4", "video/mp4")

    assert fake.names_in(made.id) == ["a.mp4", "c.mp4"]
    assert fake.names_in(by_hand) == ["b.mp4"]


def test_scoped_drive_trashes_only_inside_its_root(tmp_path):
    fake, person, root, video = _scoped(tmp_path)
    scoped = ScopedDrive(person, root)
    own = scoped.create_folder(root, "TRIALS")
    made = scoped.upload(own.id, video, "a.mp4", "video/mp4")
    live = fake.seed_folder("ALL ACCOUNTS", shared=True)
    inside_live = fake.seed_folder("TRIALS", parent=live)
    other = fake.add_file(inside_live, "VID_1.mp4", "video/mp4")
    scoped.folder(inside_live)

    scoped.trash_file(made.id)

    with pytest.raises(DriveError):
        scoped.trash_file(other)
    assert fake.names_in(own.id) == []
    assert fake.names_in(inside_live) == ["VID_1.mp4"]


def test_scoped_drive_writes_nowhere_outside_its_root(tmp_path):
    fake, person, root, video = _scoped(tmp_path)
    scoped = ScopedDrive(person, root)
    live = fake.seed_folder("ALL ACCOUNTS", shared=True)
    inside_live = fake.seed_folder("GOOD REELS", parent=live)
    scoped.folder(inside_live)
    scoped.find_folder(live, "GOOD REELS")

    with pytest.raises(DriveError):
        scoped.create_folder("root", "DEV - ALL ACCOUNTS")
    with pytest.raises(DriveError):
        scoped.create_folder(live, "TRIAL REELS")
    with pytest.raises(DriveError):
        scoped.upload(inside_live, video, "a.mp4", "video/mp4")
    assert fake.names_in(inside_live) == []
    assert fake.writes == ["create"]
