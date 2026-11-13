"""/drive: the Instagram logins that get a folder in the shared Drive
folder, and what the page shows of each: what waits to be posted (the files
in the account folder) and what was posted (its POSTED folder)."""

import datetime

from pydantic import BaseModel, Field

from ofmhelpers.schemas.devices import Device, _compact
from ofmhelpers.schemas.gdrive import DriveFile, folder_url


class DriveTarget(BaseModel):
    """An Instagram login signed in on an active phone (the first one by
    label, when on several)."""

    login_id: int
    handle: str
    device: Device
    # Its /accounts follower count (orders /drive); None until tracked.
    followers: int | None = None

    @property
    def followers_label(self) -> str:
        """ "1.7k followers"; "" until tracked."""
        if self.followers is None:
            return ""
        n = self.followers
        return f"{_compact(n)} follower{'' if n == 1 else 's'}"

    @property
    def owner(self) -> str:
        """The phone's owner, or its label when it has none."""
        return self.device.owner or self.device.label

    @property
    def folder_name(self) -> str:
        return f"{self.handle} - {self.owner}"


class UploadPick(BaseModel):
    """/drive?upload=<job>: a finished /clone video waiting for its folder
    (the page shows it on top; a click on an account's card uploads it)."""

    job_id: str
    name: str
    preview_url: str
    poster_url: str | None = None
    # Logins whose folder has it already (or it is on its way there).
    already: list[int] = Field(default_factory=list)


class UploadTarget(BaseModel):
    """An account folder clone results can go into: an Instagram login's
    "<handle> - <owner>", with what the Drive dialog sorts and groups by:
    its model (the account's /accounts creator), how many files wait there
    unposted (the folder's last /drive read; None: never read) and its
    followers."""

    login_id: int
    folder_name: str
    handle: str = ""
    owner: str = ""
    creator: str | None = None
    waiting: int | None = None
    followers: int | None = None

    @property
    def followers_label(self) -> str:
        """ "12.3k followers"; "" when not tracked or not scanned yet."""
        if self.followers is None:
            return ""
        n = self.followers
        return f"{_compact(n)} follower{'' if n == 1 else 's'}"


class DriveUpload(BaseModel):
    """A result file's upload into an account folder (`drive_uploads`)."""

    id: int
    job_id: str
    file_index: int
    login_id: int
    folder_name: str
    status: str
    drive_url: str | None = None
    error: str | None = None


class UploadRequest(BaseModel):
    """/clone "Upload to Drive": these jobs' results into each of these
    logins' folders."""

    job_ids: list[str] = Field(min_length=1, max_length=200)
    login_ids: list[int] = Field(min_length=1, max_length=50)


class UploadsQueued(BaseModel):
    queued: int
    uploads: list[DriveUpload]


class StoredFolder(BaseModel):
    login_id: int
    folder_id: str
    name: str
    posted_folder_id: str | None = None
    # The root it was made in (the owner's `drive_root_id` then).
    root_folder_id: str = ""
    # The folder's last read, and when (/drive opens with it).
    scan: AccountScan | None = None
    scanned_at: float | None = None


class StoredFolders(BaseModel):
    """The active logins' folders, each in its phone owner's current root."""

    folders: list[StoredFolder] = Field(default_factory=list)

    def get(self, login_id: int) -> StoredFolder | None:
        """The login's folder; None when none was made (or found) yet."""
        return next((f for f in self.folders if f.login_id == login_id), None)


class SyncReport(BaseModel):
    """One sweep: what it made, found, renamed, recorded, and could not do."""

    created: list[str] = Field(default_factory=list)
    adopted: list[str] = Field(default_factory=list)
    renamed: list[str] = Field(default_factory=list)
    # POSTED folders made or found (each "<account folder>/<name>").
    posted_folders: list[str] = Field(default_factory=list)
    # Owners whose phones have Instagram logins but no Google Drive
    # connected: their folders wait until they connect it on /account.
    not_connected: list[str] = Field(default_factory=list)
    # Files seen in a POSTED folder for the first time.
    newly_posted: int = 0
    failed: list[str] = Field(default_factory=list)
    # Another sweep held the lock: this one did nothing.
    busy: bool = False


class AccountScan(BaseModel):
    """One account folder as Drive has it now."""

    # Its name in Drive (renamed by hand, it differs from the login's).
    name: str = ""
    # Files directly in the account folder: uploaded, not posted yet.
    waiting: list[DriveFile] = Field(default_factory=list)
    # Files in its POSTED folder(s).
    posted: list[DriveFile] = Field(default_factory=list)
    # Its POSTED folders: the one the app keeps (under whatever name it has
    # now) and any other POSTED-named one, whose files count too.
    posted_folders: list[DriveFile] = Field(default_factory=list)
    # The POSTED-named ones beyond the kept one.
    extra_posted_folders: list[DriveFile] = Field(default_factory=list)


class UploadedToday(BaseModel):
    """The files put into an account's folder today (by Drive's own
    `createdTime`, wherever they are now: still waiting or moved to POSTED):
    `clone` = sent by /clone's "Upload to Drive" (`drive_uploads`), `manual`
    = put there by hand in Drive."""

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
    # Shown from the last read; this folder was never read yet.
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
    # Today in the Drive timezone: what "uploaded today" means.
    day: datetime.date
    cards: list[DriveCard] = Field(default_factory=list)
    # Folders in the root no login owns: hand-made, old, misspelt.
    other_folders: list[DriveFile] = Field(default_factory=list)
    # Why nothing can be shown (not set up, not shared); None when fine.
    problem: str | None = None
    # The viewer has not connected Google Drive: the page offers it.
    connect_drive: bool = False
    # False: built from the last reads (`as_of`, the oldest of them), not
    # from Drive now; the page fetches the live one (`/drive/live`).
    live: bool = True
    as_of: float | None = None
    # The viewer's own root folder, its name, and whether their Google
    # Drive is connected (Sync and their folders need it).
    root_folder_id: str = ""
    root_name: str = ""
    connected: bool = False
    posted_folder_name: str = "POSTED"

    @property
    def root_url(self) -> str | None:
        return folder_url(self.root_folder_id) if self.root_folder_id else None
