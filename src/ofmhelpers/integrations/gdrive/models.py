"""What Drive answers, as models: a folder, and any item in one."""

import mimetypes
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

FOLDER_MIME = "application/vnd.google-apps.folder"
_FOLDER_URL = "https://drive.google.com/drive/folders/{}"


def folder_url(folder_id: str) -> str:
    return _FOLDER_URL.format(folder_id)


class DriveFolder(BaseModel):
    id: str
    name: str
    # The folders it sits in: tells a folder dragged somewhere else.
    parents: list[str] = Field(default_factory=list)

    @property
    def url(self) -> str:
        return folder_url(self.id)


class DriveStorage(BaseModel):
    """A person's Drive space (`about.storageQuota`): bytes used of
    `limit`; None = no limit (a Workspace pool)."""

    usage: int
    limit: int | None = None

    @property
    def free(self) -> int | None:
        return None if self.limit is None else max(self.limit - self.usage, 0)


class DriveFile(BaseModel):
    """One item in a folder (`Drive.children`), fields named the way
    Drive's `files` resource names them."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str
    mime_type: str = Field(alias="mimeType")
    web_view_link: str = Field(default="", alias="webViewLink")
    has_thumbnail: bool = Field(default=False, alias="hasThumbnail")
    created_time: datetime | None = Field(default=None, alias="createdTime")
    size: int | None = None
    # Only `Drive.file` asks for these three.
    parents: list[str] = Field(default_factory=list)
    thumbnail_link: str = Field(default="", alias="thumbnailLink")
    owned_by_me: bool = Field(default=False, alias="ownedByMe")

    @property
    def is_folder(self) -> bool:
        return self.mime_type == FOLDER_MIME

    @property
    def kind(self) -> str:
        """What the page draws: image, video, folder or file."""
        if self.is_folder:
            return "folder"
        major = self.mime_type.split("/", 1)[0]
        if major in ("image", "video"):
            return major
        # Drive often reports a phone's upload as application/octet-stream;
        # the name's own type then says whether it is a video or an image, so
        # a reel still gets the play icon rather than the generic file one.
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
