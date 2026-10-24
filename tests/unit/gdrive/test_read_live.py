"""Live-read mode (OFM_DRIVE_READ_LIVE): dev reads the live "ALL ACCOUNTS"
folders to debug prod, with every write to Drive blocked so the live folders
can never be changed from dev."""

import os
from pathlib import Path

os.environ.setdefault("SESSION_SECRET", "test-secret")

import pytest

from ofmhelpers.gdrive import DriveError, ReadOnlyDrive, connect, read_live_mode
from ofmhelpers.gdrive.models import DriveFolder
from ofmhelpers.web.drive_folders import _is_root, root_name


class _Reads:
    """A stand-in Drive that records the reads delegated to it."""

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
    assert read_live_mode() is False


def test_read_live_mode_is_on_in_dev_when_set(monkeypatch):
    monkeypatch.setenv("OFM_DRIVE_READ_LIVE", "1")
    monkeypatch.setenv("OFM_ENV", "development")
    assert read_live_mode() is True


def test_read_live_mode_is_off_by_default(monkeypatch):
    monkeypatch.delenv("OFM_DRIVE_READ_LIVE", raising=False)
    monkeypatch.setenv("OFM_ENV", "development")
    assert read_live_mode() is False


@pytest.mark.usefixtures("fake_externals")
def test_connect_hands_back_a_read_only_drive_in_live_mode(monkeypatch):
    monkeypatch.setenv("OFM_DRIVE_READ_LIVE", "1")
    monkeypatch.setenv("OFM_ENV", "development")
    assert isinstance(connect(), ReadOnlyDrive)

    monkeypatch.setenv("OFM_DRIVE_READ_LIVE", "0")
    assert not isinstance(connect(), ReadOnlyDrive)


def test_live_mode_reads_the_live_root_name_and_accepts_it(monkeypatch):
    monkeypatch.setenv("OFM_DRIVE_READ_LIVE", "1")
    monkeypatch.setenv("OFM_ENV", "development")
    assert root_name() == "ALL ACCOUNTS"
    # A stored root of any name is accepted for reading in live mode.
    assert _is_root(DriveFolder(id="x", name="ALL ACCOUNTS")) is True
    assert _is_root(DriveFolder(id="x", name="something else")) is True


def test_dev_without_live_mode_keeps_the_sandbox_root(monkeypatch):
    monkeypatch.delenv("OFM_DRIVE_READ_LIVE", raising=False)
    monkeypatch.setenv("OFM_ENV", "development")
    assert root_name() == "DEV - ALL ACCOUNTS"
    assert _is_root(DriveFolder(id="x", name="ALL ACCOUNTS")) is False
