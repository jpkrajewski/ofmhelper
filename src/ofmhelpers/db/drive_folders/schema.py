# Copyright (C) 2026 jpkrajewski

import mimetypes
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

FOLDER_MIME = "application/vnd.google-apps.folder"


def folder_url(folder_id: str) -> str:
    return f"/drive/folders/{folder_id}"


class DriveFile(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str
    mime_type: str = Field(default="", alias="mimeType")
    is_folder: bool = False
    web_view_link: str = Field(default="", alias="webViewLink")
    has_thumbnail: bool = Field(default=False, alias="hasThumbnail")
    created_time: datetime | None = Field(default=None, alias="createdTime")
    size: int | None = None
    parents: list[str] = Field(default_factory=list)
    thumbnail_link: str = Field(default="", alias="thumbnailLink")
    owned_by_me: bool = Field(default=False, alias="ownedByMe")

    @model_validator(mode="before")
    @classmethod
    def _folder_from_mime_type(cls, data: object) -> object:
        if isinstance(data, dict) and "is_folder" not in data:
            mime_type = data.get("mimeType", data.get("mime_type", ""))
            return {**data, "is_folder": mime_type == FOLDER_MIME}
        return data

    @property
    def kind(self) -> str:
        if self.is_folder:
            return "folder"
        major = self.mime_type.split("/", 1)[0]
        if major in ("image", "video"):
            return major
        guessed, _ = mimetypes.guess_type(self.name)
        major = guessed.split("/", 1)[0] if guessed else ""
        return major if major in ("image", "video") else "file"

    @property
    def url(self) -> str:
        if self.web_view_link:
            return self.web_view_link
        if self.is_folder:
            return folder_url(self.id)
        return f"https://drive.google.com/file/d/{self.id}/view"


class PostedSeen(BaseModel, frozen=True):
    login_id: int
    folder_id: str
    files: list[DriveFile]


class UploadPick(BaseModel):
    job_id: str
    name: str
    preview_url: str
    poster_url: str | None = None
    already: list[int] = Field(default_factory=list)
    already_vas: list[int] = Field(default_factory=list)
    back_url: str = "/clone"


class DriveUpload(BaseModel):
    id: int
    job_id: str
    file_index: int
    login_id: int | None = None
    va_id: int | None = None
    folder_name: str
    status: str
    drive_url: str | None = None
    error: str | None = None


class UploadRequest(BaseModel):
    job_ids: list[str] = Field(min_length=1, max_length=200)
    login_ids: list[int] = Field(default_factory=list, max_length=50)
    va_ids: list[int] = Field(default_factory=list, max_length=50)


class UploadsQueued(BaseModel):
    queued: int
    uploads: list[DriveUpload]


class StoredFolder(BaseModel):
    login_id: int
    folder_id: str
    name: str
    posted_folder_id: str | None = None
    root_folder_id: str = ""
    scan: AccountScan | None = None
    scanned_at: float | None = None


class VaFolder(BaseModel):
    user_id: int
    folder_id: str
    name: str
    root_folder_id: str

    @property
    def url(self) -> str:
        return folder_url(self.folder_id)


class StoredFolders(BaseModel):
    folders: list[StoredFolder] = Field(default_factory=list)

    def get(self, login_id: int) -> StoredFolder | None:
        return next((f for f in self.folders if f.login_id == login_id), None)


class SyncReport(BaseModel):
    created: list[str] = Field(default_factory=list)
    adopted: list[str] = Field(default_factory=list)
    renamed: list[str] = Field(default_factory=list)
    posted_folders: list[str] = Field(default_factory=list)
    not_connected: list[str] = Field(default_factory=list)
    newly_posted: int = 0
    failed: list[str] = Field(default_factory=list)
    busy: bool = False


class AccountScan(BaseModel):
    name: str = ""
    waiting: list[DriveFile] = Field(default_factory=list)
    posted: list[DriveFile] = Field(default_factory=list)
    posted_folders: list[DriveFile] = Field(default_factory=list)
    extra_posted_folders: list[DriveFile] = Field(default_factory=list)


class UploadedToday(BaseModel):
    clone: int = 0
    manual: int = 0

    @property
    def total(self) -> int:
        return self.clone + self.manual
