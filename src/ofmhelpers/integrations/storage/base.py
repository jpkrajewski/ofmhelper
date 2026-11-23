# Copyright (C) 2026 jpkrajewski

from typing import Protocol
lazy from pathlib import Path

lazy from ofmhelpers.db.drive_folders.schema import DriveFile
lazy from ofmhelpers.integrations.storage.models import (
    DriveDownload,
    DriveFolder,
    DriveListing,
    DriveSheet,
    DriveStorage,
    DriveThumb,
)


class DriveError(Exception): ...


class FolderGoneError(DriveError): ...


class FolderTrashedError(FolderGoneError): ...


RECONNECT = "Reconnect Google Drive on your Account page"


class DriveAuthError(DriveError):
    def __init__(self, message: str = RECONNECT) -> None:
        super().__init__(message)


class DriveFullError(DriveError): ...


class DriveNotSetUpError(DriveError): ...


class StorageClient(Protocol):
    def folder(self, folder_id: str) -> DriveFolder: ...

    def children(
        self,
        parent_id: str,
        *,
        folders_only: bool = False,
    ) -> list[DriveFile]: ...

    def children_of(self, parent_ids: list[str]) -> DriveListing: ...

    def find_folder(self, parent_id: str, name: str) -> DriveFolder | None: ...

    def create_folder(self, parent_id: str, name: str) -> DriveFolder: ...

    def ensure_folder(self, parent_id: str, name: str) -> DriveFolder: ...

    def rename(self, folder_id: str, name: str) -> DriveFolder: ...

    def file(self, file_id: str) -> DriveFile: ...

    def find_file(self, parent_id: str, name: str) -> DriveFile | None: ...

    def sheet(self, file_id: str) -> DriveSheet: ...

    def find_sheet(self, parent_id: str, name: str) -> DriveFile | None: ...

    def create_sheet(self, parent_id: str, name: str, content: bytes) -> DriveFile: ...

    def thumbnail(self, item: DriveFile, px: int) -> DriveThumb | None: ...

    def download(self, file_id: str) -> DriveDownload: ...

    def upload(
        self,
        parent_id: str,
        path: Path,
        name: str,
        mime_type: str,
    ) -> DriveFile: ...

    def web_url(self, item_id: str) -> str: ...

    def trash_file(self, item_id: str) -> None: ...

    def storage(self) -> DriveStorage: ...
