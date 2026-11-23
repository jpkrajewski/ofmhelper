from datetime import date
from typing import Literal, get_args

from pydantic import BaseModel, Field, SkipValidation

from ofmhelpers.config.types import AccountFolder
from ofmhelpers.db.drive_folders.models import DriveBoard, DriveTarget
from ofmhelpers.db.drive_folders.schema import (
    AccountScan,
    DriveFile,
    UploadPick,
    VaFolder,
)
from ofmhelpers.features.storage.constants import StorageConstants
from ofmhelpers.integrations.downloaders.urls import NotALinkError, read_links
from ofmhelpers.integrations.storage import DriveSheet, DriveStorage, StorageClient

BinPart = Literal[AccountFolder.TRIALS, AccountFolder.SPOOFED, AccountFolder.THREADS]


def bin_parts() -> list[AccountFolder]:
    return list(get_args(BinPart))


class Sizes(BaseModel):
    total: int = 0
    parts: dict[AccountFolder, int] = Field(default_factory=dict)

    def add(self, size: int, part: AccountFolder | None) -> None:
        self.total += size
        if part is not None:
            self.parts[part] = self.parts.get(part, 0) + size

    def of(self, part: AccountFolder) -> int:
        return self.parts.get(part, 0)

    def merge(self, other: Sizes) -> None:
        self.total += other.total
        for part, size in other.parts.items():
            self.parts[part] = self.parts.get(part, 0) + size


class FolderUsage(BaseModel):
    login_id: int
    folder_id: str
    name: str
    sizes: Sizes = Field(default_factory=Sizes)


class StorageUsage(BaseModel):
    quota: DriveStorage
    folders: list[FolderUsage] = Field(default_factory=list)
    scanned_at: float

    @property
    def totals(self) -> Sizes:
        found = Sizes()
        for folder in self.folders:
            found.merge(folder.sizes)
        return found


class BinMove(BaseModel):
    part: BinPart
    folder_id: str | None = None


class Moved(BinMove):
    items: int = 0
    bytes: int = 0


class StoragePage(BaseModel):
    connected: bool
    usage: StorageUsage | None = None
    scanning: bool = False
    scan_failed: bool = False
    moving: BinMove | None = None
    moved: Moved | None = None
    move_failed: bool = False
    bin_url: str
    parts: list[AccountFolder] = Field(default_factory=bin_parts)


class _Place(BaseModel, frozen=True):
    sizes: Sizes
    part: AccountFolder | None = None
    top: bool = False

    def below(self, item: DriveFile) -> _Place:
        named = self.top and item.name in bin_parts()
        return _Place(
            sizes=self.sizes, part=AccountFolder(item.name) if named else self.part
        )


class BotRoot(BaseModel, frozen=True):
    folder_id: str
    email: str


class StorageAccess(BaseModel, frozen=True, arbitrary_types_allowed=True):
    owner_id: int
    client: SkipValidation[StorageClient] | None
    constants: StorageConstants
    bot: BotRoot | None = None
    read_live: bool = False
    session: str = ""


class ReviewPlace(BaseModel, frozen=True, arbitrary_types_allowed=True):
    drive: SkipValidation[StorageClient]
    folder_id: str


class _OwnerTargets(BaseModel):
    user_id: int
    targets: list[DriveTarget]


class _Scanned(BaseModel):
    scan: AccountScan
    items: list[DriveFile]


class VaTarget(BaseModel):
    owner_id: int
    folder: VaFolder


class PageView(BaseModel):
    board: DriveBoard
    pick: UploadPick | None


class _Read(BaseModel):
    storage: DriveStorage | None = None
    reconnect: bool = False


class SheetColumn(BaseModel, frozen=True):
    links: list[str]
    skipped: int = 0


def _cell_links(cell: date | str | None) -> list[str] | None:
    if cell is None:
        return []
    if not isinstance(cell, str):
        return None
    try:
        return read_links(cell)
    except NotALinkError:  # silent: a cell without a link
        return None


class Sheet(DriveSheet):
    name: str

    def column(self, day: date) -> SheetColumn | None:
        found = False
        links: list[str] = []
        skipped = 0
        for tab in self.tabs:
            header = tab.rows[0] if tab.rows else []
            for index, top in enumerate(header):
                if top != day:
                    continue
                found = True
                for row in tab.rows[1:]:
                    read = _cell_links(row[index] if index < len(row) else None)
                    if read is None:
                        skipped += 1
                    else:
                        links.extend(read)
        if not found:
            return None
        return SheetColumn(links=list(dict.fromkeys(links)), skipped=skipped)
