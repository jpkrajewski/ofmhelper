# Copyright (C) 2026 jpkrajewski

from pydantic import BaseModel, ConfigDict, Field

from ofmhelpers.db.drive_folders.schema import DriveFile

SHEET_MIME = "application/vnd.google-apps.spreadsheet"


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
            self.model_dump(by_alias=True, exclude_unset=True),
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
