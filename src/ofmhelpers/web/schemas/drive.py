"""/drive: the Instagram logins that get a folder in the shared Drive
folder, and what the page shows of each one's day."""

import datetime

from pydantic import BaseModel, Field

from ofmhelpers.gdrive import DriveFile, folder_url
from ofmhelpers.web.schemas.devices import Device


def day_folder_name(day: datetime.date) -> str:
    """ "2026-10-21": the year in it, so a folder found by this name is
    never last year's, and Drive sorts them by date."""
    return day.isoformat()


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


class SyncReport(BaseModel):
    """One sweep: what it made, found, renamed, and could not do."""

    created: list[str] = Field(default_factory=list)
    adopted: list[str] = Field(default_factory=list)
    renamed: list[str] = Field(default_factory=list)
    days: int = 0
    failed: list[str] = Field(default_factory=list)
    # Another sweep held the lock: this one did nothing.
    busy: bool = False


class DriveCard(BaseModel):
    target: DriveTarget
    account_folder_id: str | None = None
    day_folder_id: str | None = None
    files: list[DriveFile] = Field(default_factory=list)
    error: str | None = None

    @property
    def account_url(self) -> str | None:
        return folder_url(self.account_folder_id) if self.account_folder_id else None

    @property
    def day_url(self) -> str | None:
        return folder_url(self.day_folder_id) if self.day_folder_id else None

    def count(self, kind: str) -> int:
        return sum(1 for f in self.files if f.kind == kind)

    @property
    def search_text(self) -> str:
        t = self.target
        return f"{t.handle} {t.owner} {t.device.label}".lower()


class DriveBoard(BaseModel):
    day: datetime.date
    cards: list[DriveCard] = Field(default_factory=list)
    # Folders in the root no login owns: hand-made, old, misspelt.
    other_folders: list[DriveFile] = Field(default_factory=list)
    # Why nothing can be shown (not set up, not shared); None when fine.
    problem: str | None = None
    bot_email: str | None = None
    root_folder_id: str = ""

    @property
    def day_label(self) -> str:
        return day_folder_name(self.day)

    @property
    def root_url(self) -> str | None:
        return folder_url(self.root_folder_id) if self.root_folder_id else None

    @property
    def filled(self) -> int:
        return sum(1 for c in self.cards if c.files)

    @property
    def uploads(self) -> int:
        return sum(len(c.files) for c in self.cards)
