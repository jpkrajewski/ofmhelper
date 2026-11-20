from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from ofmhelpers.db.drive_folders.schema import FOLDER_MIME, DriveFile, folder_url

__all__ = [
    "FOLDER_MIME",
    "SHEET_MIME",
    "DriveAbout",
    "DriveAck",
    "DriveDownload",
    "DriveFile",
    "DriveFolder",
    "DriveListing",
    "DriveRow",
    "DriveRowList",
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
    parents: list[str] = Field(default_factory=list)

    @property
    def url(self) -> str:
        return folder_url(self.id)


class FolderItems(BaseModel):
    folder_id: str
    items: list[DriveFile] = Field(default_factory=list)


class DriveListing(BaseModel):
    folders: list[FolderItems] = Field(default_factory=list)

    def of(self, folder_id: str) -> list[DriveFile]:
        for folder in self.folders:
            if folder.folder_id == folder_id:
                return folder.items
        return []


class DriveThumb(BaseModel):
    model_config = ConfigDict(frozen=True)

    content: bytes
    media_type: str


class DriveDownload(BaseModel):
    model_config = ConfigDict(frozen=True)

    content: bytes
    name: str


class SheetTab(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    rows: list[list[date | str | None]] = Field(default_factory=list)


class DriveSheet(BaseModel):
    model_config = ConfigDict(frozen=True)

    tabs: list[SheetTab] = Field(default_factory=list)


class DriveRow(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    id: str
    name: str
    mime_type: str | None = Field(default=None, alias="mimeType")
    trashed: bool | None = None
    parents: list[str] = Field(default_factory=list)
    created_time: str = Field(default="", alias="createdTime")

    def as_file(self) -> DriveFile:
        return DriveFile.model_validate(
            self.model_dump(by_alias=True, exclude_unset=True)
        )


class DriveRowList(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    files: list[DriveRow] = Field(default_factory=list)
    next_page_token: str | None = Field(default=None, alias="nextPageToken")


class DriveAck(BaseModel):
    model_config = ConfigDict(extra="allow")


class DriveQuota(BaseModel):
    usage: int = 0
    limit: int | None = None


class DriveAbout(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    storage_quota: DriveQuota = Field(default_factory=DriveQuota, alias="storageQuota")


class DriveStorage(BaseModel):
    usage: int
    limit: int | None = None

    @property
    def free(self) -> int | None:
        return None if self.limit is None else max(self.limit - self.usage, 0)
