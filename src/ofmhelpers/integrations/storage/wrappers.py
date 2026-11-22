import threading
lazy from collections.abc import Callable
lazy from pathlib import Path

from ofmhelpers.integrations.storage.base import DriveError, StorageClient
from ofmhelpers.integrations.storage.constants import client_constants
lazy from ofmhelpers.db.drive_folders.schema import DriveFile
lazy from ofmhelpers.integrations.storage.models import (
    DriveDownload,
    DriveFolder,
    DriveListing,
    DriveSheet,
    DriveStorage,
    DriveThumb,
)


class ReadOnlyDrive:
    def __init__(self, inner: StorageClient) -> None:
        self._inner = inner

    def folder(self, folder_id: str) -> DriveFolder:
        return self._inner.folder(folder_id)

    def children(
        self, parent_id: str, *, folders_only: bool = False
    ) -> list[DriveFile]:
        return self._inner.children(parent_id, folders_only=folders_only)

    def children_of(self, parent_ids: list[str]) -> DriveListing:
        return self._inner.children_of(parent_ids)

    def find_folder(self, parent_id: str, name: str) -> DriveFolder | None:
        return self._inner.find_folder(parent_id, name)

    def file(self, file_id: str) -> DriveFile:
        return self._inner.file(file_id)

    def find_file(self, parent_id: str, name: str) -> DriveFile | None:
        return self._inner.find_file(parent_id, name)

    def sheet(self, file_id: str) -> DriveSheet:
        return self._inner.sheet(file_id)

    def thumbnail(self, item: DriveFile, px: int) -> DriveThumb | None:
        return self._inner.thumbnail(item, px)

    def download(self, file_id: str) -> DriveDownload:
        return self._inner.download(file_id)

    def storage(self) -> DriveStorage:
        return self._inner.storage()

    def create_folder(self, parent_id: str, name: str) -> DriveFolder:  # noqa: ARG002
        raise DriveError(client_constants().read_only)

    def ensure_folder(self, parent_id: str, name: str) -> DriveFolder:  # noqa: ARG002
        raise DriveError(client_constants().read_only)

    def rename(self, folder_id: str, name: str) -> DriveFolder:  # noqa: ARG002
        raise DriveError(client_constants().read_only)

    def upload(
        self,
        parent_id: str,  # noqa: ARG002
        path: Path,  # noqa: ARG002
        name: str,  # noqa: ARG002
        mime_type: str,  # noqa: ARG002
    ) -> DriveFile:
        raise DriveError(client_constants().read_only)

    def grant(self, item_id: str, email: str) -> None:  # noqa: ARG002
        raise DriveError(client_constants().read_only)

    def trash_file(self, item_id: str) -> None:  # noqa: ARG002
        raise DriveError(client_constants().read_only)


class ScopedDrive(ReadOnlyDrive):
    def __init__(self, inner: StorageClient, root_id: str) -> None:
        super().__init__(inner)
        self._writable = {root_id}

    def _inside(self, parent_id: str) -> None:
        if parent_id not in self._writable:
            raise DriveError(client_constants().read_only)

    def _adopt(self, parents: list[str], folder: DriveFolder) -> DriveFolder:
        if self._writable.intersection(parents):
            self._writable.add(folder.id)
        return folder

    def folder(self, folder_id: str) -> DriveFolder:
        found = self._inner.folder(folder_id)
        return self._adopt(found.parents, found)

    def find_folder(self, parent_id: str, name: str) -> DriveFolder | None:
        found = self._inner.find_folder(parent_id, name)
        return self._adopt([parent_id], found) if found else None

    def create_folder(self, parent_id: str, name: str) -> DriveFolder:
        self._inside(parent_id)
        return self._adopt([parent_id], self._inner.create_folder(parent_id, name))

    def ensure_folder(self, parent_id: str, name: str) -> DriveFolder:
        return self.find_folder(parent_id, name) or self.create_folder(parent_id, name)

    def upload(
        self, parent_id: str, path: Path, name: str, mime_type: str
    ) -> DriveFile:
        self._inside(parent_id)
        return self._inner.upload(parent_id, path, name, mime_type)

    def trash_file(self, item_id: str) -> None:
        if not self._writable.intersection(self._inner.file(item_id).parents):
            raise DriveError(client_constants().read_only)
        self._inner.trash_file(item_id)


class _Opened(threading.local):
    def __init__(self) -> None:
        self.drive: StorageClient | None = None


class PerThreadDrive:
    def __init__(self, open_drive: Callable[[], StorageClient]) -> None:
        self._open_drive = open_drive
        self._opened = _Opened()

    def _drive(self) -> StorageClient:
        if self._opened.drive is None:
            self._opened.drive = self._open_drive()
        return self._opened.drive

    def folder(self, folder_id: str) -> DriveFolder:
        return self._drive().folder(folder_id)

    def children(
        self, parent_id: str, *, folders_only: bool = False
    ) -> list[DriveFile]:
        return self._drive().children(parent_id, folders_only=folders_only)

    def children_of(self, parent_ids: list[str]) -> DriveListing:
        return self._drive().children_of(parent_ids)

    def find_folder(self, parent_id: str, name: str) -> DriveFolder | None:
        return self._drive().find_folder(parent_id, name)

    def create_folder(self, parent_id: str, name: str) -> DriveFolder:
        return self._drive().create_folder(parent_id, name)

    def ensure_folder(self, parent_id: str, name: str) -> DriveFolder:
        return self._drive().ensure_folder(parent_id, name)

    def rename(self, folder_id: str, name: str) -> DriveFolder:
        return self._drive().rename(folder_id, name)

    def file(self, file_id: str) -> DriveFile:
        return self._drive().file(file_id)

    def find_file(self, parent_id: str, name: str) -> DriveFile | None:
        return self._drive().find_file(parent_id, name)

    def sheet(self, file_id: str) -> DriveSheet:
        return self._drive().sheet(file_id)

    def thumbnail(self, item: DriveFile, px: int) -> DriveThumb | None:
        return self._drive().thumbnail(item, px)

    def download(self, file_id: str) -> DriveDownload:
        return self._drive().download(file_id)

    def upload(
        self, parent_id: str, path: Path, name: str, mime_type: str
    ) -> DriveFile:
        return self._drive().upload(parent_id, path, name, mime_type)

    def grant(self, item_id: str, email: str) -> None:
        self._drive().grant(item_id, email)

    def trash_file(self, item_id: str) -> None:
        self._drive().trash_file(item_id)

    def storage(self) -> DriveStorage:
        return self._drive().storage()
