"""What every Drive (`client.Drive`, `fake.FakeDrive`) offers, and how it
fails. Callers type against `DriveApi`, never a concrete class."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from ofmhelpers.gdrive.models import DriveFile, DriveFolder


class DriveError(Exception):
    """Drive is not set up, refused, or failed; the message says which
    (/drive shows it as is)."""


class FolderGoneError(DriveError):
    """Missing, trashed, not a folder, or not shared with the account."""


class DriveApi(Protocol):
    def folder(self, folder_id: str) -> DriveFolder: ...

    def children(
        self, parent_id: str, *, folders_only: bool = False
    ) -> list[DriveFile]: ...

    def find_folder(self, parent_id: str, name: str) -> DriveFolder | None: ...

    def create_folder(self, parent_id: str, name: str) -> DriveFolder: ...

    def ensure_folder(self, parent_id: str, name: str) -> DriveFolder: ...

    def rename(self, folder_id: str, name: str) -> DriveFolder: ...

    def file(self, file_id: str) -> DriveFile: ...

    def thumbnail(self, item: DriveFile, px: int) -> tuple[bytes, str] | None: ...
