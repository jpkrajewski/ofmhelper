"""What every Drive (`client.Drive`, `fake.FakeDrive`) offers, and how it
fails. Callers type against `DriveApi`, never a concrete class."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from pathlib import Path

    from ofmhelpers.gdrive.models import DriveFile, DriveFolder, DriveStorage


class DriveError(Exception):
    """Drive is not set up, refused, or failed; the message says which
    (/drive shows it as is)."""


class FolderGoneError(DriveError):
    """Missing, trashed, not a folder, or not shared with the account."""


RECONNECT = "Reconnect Google Drive on your Account page"


class DriveAuthError(DriveError):
    """Google refused the person's sign-in: the grant was revoked or
    expired, or Drive was not ticked on Google's consent screen. Only
    connecting Google Drive again on /account fixes it. The message is safe
    to show; Google's own text stays on the chained cause and in the log."""

    def __init__(self, message: str = RECONNECT) -> None:
        super().__init__(message)


class DriveFullError(DriveError):
    """The person's Drive has no room left for the upload (Drive's
    `storageQuotaExceeded`)."""


class DriveApi(Protocol):
    def folder(self, folder_id: str) -> DriveFolder: ...

    def children(
        self, parent_id: str, *, folders_only: bool = False
    ) -> list[DriveFile]: ...

    def children_of(self, parent_ids: list[str]) -> dict[str, list[DriveFile]]: ...

    def find_folder(self, parent_id: str, name: str) -> DriveFolder | None: ...

    def create_folder(self, parent_id: str, name: str) -> DriveFolder: ...

    def ensure_folder(self, parent_id: str, name: str) -> DriveFolder: ...

    def rename(self, folder_id: str, name: str) -> DriveFolder: ...

    def file(self, file_id: str) -> DriveFile: ...

    def thumbnail(self, item: DriveFile, px: int) -> tuple[bytes, str] | None: ...

    def download(self, file_id: str) -> tuple[bytes, str]:
        """A file's content and name; `FolderGoneError` when missing."""
        ...

    def upload(
        self, parent_id: str, path: Path, name: str, mime_type: str
    ) -> DriveFile: ...

    def grant(self, item_id: str, email: str) -> None: ...

    def trash_file(self, item_id: str) -> None: ...

    def storage(self) -> DriveStorage: ...
