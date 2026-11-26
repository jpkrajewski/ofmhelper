# Copyright (C) 2026 jpkrajewski

from datetime import date

from pydantic import BaseModel, Field

from ofmhelpers.config.types import AccountFolder
from ofmhelpers.db.devices.models import Device
from ofmhelpers.db.devices.schema import followers_text
from ofmhelpers.db.drive_folders.schema import (
    AccountScan,
    DriveFile,
    UploadedToday,
    VaFolder,
    folder_url,
)


class DriveTarget(BaseModel):
    login_id: int
    handle: str
    device: Device
    followers: int | None = None

    @property
    def followers_label(self) -> str:
        return followers_text(self.followers)

    @property
    def owner(self) -> str:
        return self.device.owner or self.device.label

    @property
    def folder_name(self) -> str:
        return f"{self.handle} - {self.owner}"


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
        return followers_text(self.followers)


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
    syncing_job_id: str | None = None
    posted_folder_name: str = AccountFolder.POSTED

    @property
    def root_url(self) -> str | None:
        return folder_url(self.root_folder_id) if self.root_folder_id else None
