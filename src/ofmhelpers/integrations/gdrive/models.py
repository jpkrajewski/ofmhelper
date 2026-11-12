"""What Drive answers, as models: a folder, and any item in one."""

from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from ofmhelpers.schemas.gdrive import FOLDER_MIME, DriveFile, folder_url

__all__ = [
    "FOLDER_MIME",
    "SHEET_MIME",
    "DriveDownload",
    "DriveFile",
    "DriveFolder",
    "DriveListing",
    "DriveSheet",
    "DriveStorage",
    "DriveThumb",
    "FolderItems",
    "SheetTab",
    "folder_url",
]

SHEET_MIME = "application/vnd.google-apps.spreadsheet"


class DriveFolder(BaseModel):
    id: str
    name: str
    # The folders it sits in: tells a folder dragged somewhere else.
    parents: list[str] = Field(default_factory=list)

    @property
    def url(self) -> str:
        return folder_url(self.id)


class FolderItems(BaseModel):
    """One folder of a `DriveListing`: what is in it, newest first."""

    folder_id: str
    items: list[DriveFile] = Field(default_factory=list)


class DriveListing(BaseModel):
    """What is in each of several folders (`children_of`), in the order
    they were asked for."""

    folders: list[FolderItems] = Field(default_factory=list)

    def of(self, folder_id: str) -> list[DriveFile]:
        """That folder's items; none for a folder that was not listed."""
        for folder in self.folders:
            if folder.folder_id == folder_id:
                return folder.items
        return []


class DriveThumb(BaseModel):
    """A file's preview image and its content type."""

    model_config = ConfigDict(frozen=True)

    content: bytes
    media_type: str


class DriveDownload(BaseModel):
    """A file's whole content and its Drive name."""

    model_config = ConfigDict(frozen=True)

    content: bytes
    name: str


class SheetTab(BaseModel):
    """One tab of a `DriveSheet`: its cells row by row, as far as the tab is
    filled. A date cell is a `date`, any other filled cell its text (a cell
    showing a label over a link: the link), an empty one None."""

    model_config = ConfigDict(frozen=True)

    name: str
    rows: list[list[date | str | None]] = Field(default_factory=list)


class DriveSheet(BaseModel):
    """A Google Sheet's content (`sheet`): every tab, in its order."""

    model_config = ConfigDict(frozen=True)

    tabs: list[SheetTab] = Field(default_factory=list)


class DriveStorage(BaseModel):
    """A person's Drive space (`about.storageQuota`): bytes used of
    `limit`; None = no limit (a Workspace pool)."""

    usage: int
    limit: int | None = None

    @property
    def free(self) -> int | None:
        return None if self.limit is None else max(self.limit - self.usage, 0)
