import mimetypes
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from ofmhelpers.config.types import AccountFolder
from ofmhelpers.db.devices.schema import Device, _compact

FOLDER_MIME = "application/vnd.google-apps.folder"


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


class DriveTarget(BaseModel):
    login_id: int
    handle: str
    device: Device
    followers: int | None = None

    @property
    def followers_label(self) -> str:
        if self.followers is None:
            return ""
        n = self.followers
        return f"{_compact(n)} follower{'' if n == 1 else 's'}"

    @property
    def owner(self) -> str:
        return self.device.owner or self.device.label

    @property
    def folder_name(self) -> str:
        return f"{self.handle} - {self.owner}"


class UploadPick(BaseModel):
    job_id: str
    name: str
    preview_url: str
    poster_url: str | None = None
    already: list[int] = Field(default_factory=list)
    already_vas: list[int] = Field(default_factory=list)
    back_url: str = "/clone"


class UploadTarget(BaseModel):
    login_id: int
    folder_name: str
    handle: str = ""
    owner: str = ""
    creator: str | None = None
    waiting: int | None = None
    followers: int | None = None

    @property
    def followers_label(self) -> str:
        if self.followers is None:
            return ""
        n = self.followers
        return f"{_compact(n)} follower{'' if n == 1 else 's'}"


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


class DriveCard(BaseModel):
    target: DriveTarget
    account_folder_id: str | None = None
    posted_folder_id: str | None = None
    scan: AccountScan = Field(default_factory=AccountScan)
    uploaded_today: UploadedToday = Field(default_factory=UploadedToday)
    error: str | None = None
    unread: bool = False

    @property
    def account_url(self) -> str | None:
        return folder_url(self.account_folder_id) if self.account_folder_id else None

    @property
    def waiting(self) -> list[DriveFile]:
        return self.scan.waiting

    @property
    def posted_total(self) -> int:
        return len(self.scan.posted)

    def count(self, kind: str) -> int:
        return sum(1 for f in self.waiting if f.kind == kind)

    @property
    def search_text(self) -> str:
        t = self.target
        return f"{t.handle} {t.owner} {t.device.label}".lower()


class DriveBoard(BaseModel):
    day: date
    cards: list[DriveCard] = Field(default_factory=list)
    va_folders: list[VaFolder] = Field(default_factory=list)
    other_folders: list[DriveFile] = Field(default_factory=list)
    problem: str | None = None
    connect_drive: bool = False
    live: bool = True
    as_of: float | None = None
    root_folder_id: str = ""
    root_name: str = ""
    connected: bool = False
    posted_folder_name: str = AccountFolder.POSTED

    @property
    def root_url(self) -> str | None:
        return folder_url(self.root_folder_id) if self.root_folder_id else None
