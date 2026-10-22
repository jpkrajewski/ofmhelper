"""/drive: the Instagram logins that get a folder in the shared Drive
folder, and what the page shows of each: what waits to be posted (the files
in the account folder) and what was posted (its POSTED folder)."""

import datetime

from pydantic import BaseModel, Field

from ofmhelpers.gdrive import DriveFile, folder_url
from ofmhelpers.web.schemas.devices import Device


class DriveTarget(BaseModel):
    """An Instagram login signed in on an active phone (the first one by
    label, when on several)."""

    login_id: int
    handle: str
    device: Device
    # Its /accounts follower count (orders /drive); None until tracked.
    followers: int | None = None

    @property
    def owner(self) -> str:
        """The phone's owner, or its label when it has none."""
        return self.device.owner or self.device.label

    @property
    def folder_name(self) -> str:
        return f"{self.handle} - {self.owner}"


class StoredFolder(BaseModel):
    login_id: int
    folder_id: str
    name: str
    posted_folder_id: str | None = None


class SyncReport(BaseModel):
    """One sweep: what it made, found, renamed, recorded, and could not do."""

    created: list[str] = Field(default_factory=list)
    adopted: list[str] = Field(default_factory=list)
    renamed: list[str] = Field(default_factory=list)
    # POSTED folders made or found (each "<account folder>/<name>").
    posted_folders: list[str] = Field(default_factory=list)
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


class DriveCard(BaseModel):
    target: DriveTarget
    account_folder_id: str | None = None
    posted_folder_id: str | None = None
    scan: AccountScan = Field(default_factory=AccountScan)
    # From the sweeps' record of when files first showed up in POSTED.
    posted_today: int = 0
    posted_week: int = 0
    error: str | None = None

    @property
    def account_url(self) -> str | None:
        return folder_url(self.account_folder_id) if self.account_folder_id else None

    @property
    def posted_url(self) -> str | None:
        return folder_url(self.posted_folder_id) if self.posted_folder_id else None

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
    # Today in the Drive timezone: what "posted today" means.
    day: datetime.date
    cards: list[DriveCard] = Field(default_factory=list)
    # Folders in the root no login owns: hand-made, old, misspelt.
    other_folders: list[DriveFile] = Field(default_factory=list)
    # Why nothing can be shown (not set up, not shared); None when fine.
    problem: str | None = None
    bot_email: str | None = None
    root_folder_id: str = ""
    posted_folder_name: str = "POSTED"

    @property
    def root_url(self) -> str | None:
        return folder_url(self.root_folder_id) if self.root_folder_id else None

    @property
    def accounts_waiting(self) -> int:
        return sum(1 for c in self.cards if c.waiting)

    @property
    def files_waiting(self) -> int:
        return sum(len(c.waiting) for c in self.cards)

    @property
    def posted_today(self) -> int:
        return sum(c.posted_today for c in self.cards)

    @property
    def posted_week(self) -> int:
        return sum(c.posted_week for c in self.cards)

    @property
    def posted_total(self) -> int:
        return sum(c.posted_total for c in self.cards)
