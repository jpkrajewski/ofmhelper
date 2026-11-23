# Copyright (C) 2026 jpkrajewski

from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from ofmhelpers.db.drive_folders.schema import DriveFile, folder_url


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


class DriveStorage(BaseModel):
    usage: int
    limit: int | None = None

    @property
    def free(self) -> int | None:
        return None if self.limit is None else max(self.limit - self.usage, 0)
