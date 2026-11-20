import mimetypes
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

FOLDER_MIME = "application/vnd.google-apps.folder"


class AccountFolder(StrEnum):
    POSTED = "POSTED"
    TRIALS = "TRIALS"
    THREADS = "THREADS"


_FOLDER_URL = "https://drive.google.com/drive/folders/{}"


def folder_url(folder_id: str) -> str:
    return _FOLDER_URL.format(folder_id)


class DriveFile(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str
    mime_type: str = Field(alias="mimeType")
    web_view_link: str = Field(default="", alias="webViewLink")
    has_thumbnail: bool = Field(default=False, alias="hasThumbnail")
    created_time: datetime | None = Field(default=None, alias="createdTime")
    size: int | None = None
    parents: list[str] = Field(default_factory=list)
    thumbnail_link: str = Field(default="", alias="thumbnailLink")
    owned_by_me: bool = Field(default=False, alias="ownedByMe")

    @property
    def is_folder(self) -> bool:
        return self.mime_type == FOLDER_MIME

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
