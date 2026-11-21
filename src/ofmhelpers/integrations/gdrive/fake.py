import fcntl
import io
import threading
from contextlib import contextmanager
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
lazy from collections.abc import Iterator

from PIL import Image
from pydantic import BaseModel, Field

from ofmhelpers.config import settings
from ofmhelpers.integrations.gdrive.base import (
    DriveAuthError,
    DriveError,
    DriveFullError,
    FolderGoneError,
    FolderTrashedError,
)
from ofmhelpers.integrations.gdrive.models import (
    FOLDER_MIME,
    SHEET_MIME,
    DriveDownload,
    DriveFile,
    DriveFolder,
    DriveListing,
    DriveSheet,
    DriveStorage,
    DriveThumb,
    FolderItems,
    SheetTab,
)

FAKE_BOT_EMAIL = "drive-bot@fake.iam.gserviceaccount.com"
DEFAULT_OWNER = "owner@example.com"
_STATE_FILE = "drive.json"


class FakeItem(BaseModel):
    id: str
    name: str
    mime_type: str = FOLDER_MIME
    parents: list[str] = Field(default_factory=list)
    owner: str = DEFAULT_OWNER
    created: int = 0
    created_time: datetime = Field(default_factory=lambda: datetime.now(UTC))
    trashed: bool = False
    has_thumbnail: bool = False
    editors: list[str] = Field(default_factory=list)
    sheet: DriveSheet | None = None
    size: int | None = None


class FakeDriveState(BaseModel):
    items: dict[str, FakeItem] = Field(default_factory=dict)
    shared: list[str] = Field(default_factory=list)
    next_id: int = 1
    calls: list[str] = Field(default_factory=list)
    fail_on: list[str] = Field(default_factory=list)
    storage_used: int = 0
    storage_limit: int | None = None
    refused: list[str] = Field(default_factory=list)


_WRITES = frozenset({"create", "rename", "upload", "grant"})
UPLOADER = "uploader@example.com"


@lru_cache(maxsize=1)
def _thumb_jpeg() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (32, 32), "teal").save(out, "JPEG")
    return out.getvalue()


class FakeDrive:
    def __init__(self, state_dir: str | Path | None = None) -> None:
        self._path = Path(state_dir) / _STATE_FILE if state_dir else None
        self._memory = FakeDriveState()
        self._lock = threading.Lock()

    @contextmanager
    def _state(self, call: str | None = None) -> Iterator[FakeDriveState]:
        with self._lock:
            if self._path is None:
                if call:
                    self._memory.calls.append(call)
                yield self._memory
                return
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a+", encoding="utf-8") as fh:
                fcntl.flock(fh, fcntl.LOCK_EX)
                fh.seek(0)
                raw = fh.read()
                state = (
                    FakeDriveState.model_validate_json(raw) if raw else FakeDriveState()
                )
                if call:
                    state.calls.append(call)
                yield state
                fh.seek(0)
                fh.truncate()
                fh.write(state.model_dump_json())

    @staticmethod
    def _new(state: FakeDriveState, **fields) -> FakeItem:
        number = state.next_id
        state.next_id += 1
        item = FakeItem(id=f"fake{number:08d}", created=number, **fields)
        state.items[item.id] = item
        return item

    @staticmethod
    def _chain(state: FakeDriveState, item_id: str) -> Iterator[FakeItem | None]:
        seen: set[str] = set()
        current: str | None = item_id
        while current is not None and current not in seen:
            seen.add(current)
            item = state.items.get(current)
            yield item
            if item is None:
                return
            current = item.parents[0] if item.parents else None

    def _visible(self, state: FakeDriveState, item_id: str) -> FakeItem | None:
        chain = list(self._chain(state, item_id))
        if any(i is None or i.trashed for i in chain):
            return None
        if not any(i is not None and i.id in state.shared for i in chain):
            return None
        return state.items[item_id]

    def _live(self, state: FakeDriveState, item_id: str, what: str) -> FakeItem:
        item = self._visible(state, item_id)
        if item is None:
            msg = f"{what}: not found, or not shared with the bot"
            raise FolderGoneError(msg)
        return item

    @staticmethod
    def _as_file(item: FakeItem) -> DriveFile:
        return DriveFile.model_validate(
            {
                "id": item.id,
                "name": item.name,
                "mimeType": item.mime_type,
                "hasThumbnail": item.has_thumbnail,
                "createdTime": item.created_time,
                "parents": list(item.parents),
                "thumbnailLink": (
                    f"fake://thumb/{item.id}=s220" if item.has_thumbnail else ""
                ),
                "ownedByMe": item.owner == FAKE_BOT_EMAIL,
                "size": item.size,
            }
        )

    def folder(self, folder_id: str) -> DriveFolder:
        with self._state("folder") as state:
            chain = list(self._chain(state, folder_id))
            shared = any(i is not None and i.id in state.shared for i in chain)
            if any(i is None for i in chain) or not shared:
                msg = f"Folder {folder_id}: not found, or not shared with the bot"
                raise FolderGoneError(msg)
            item = state.items[folder_id]
            if item.trashed or item.mime_type != FOLDER_MIME:
                msg = f"Folder {folder_id} is trashed or not a folder"
                raise FolderTrashedError(msg)
            return DriveFolder(id=item.id, name=item.name, parents=list(item.parents))

    def children(
        self, parent_id: str, *, folders_only: bool = False
    ) -> list[DriveFile]:
        with self._state("children") as state:
            chain = list(self._chain(state, parent_id))
            if any(i is None for i in chain) or not any(
                i is not None and i.id in state.shared for i in chain
            ):
                msg = (
                    f"Listing folder {parent_id}: not found, or not shared with the bot"
                )
                raise FolderGoneError(msg)
            found = [
                i
                for i in state.items.values()
                if parent_id in i.parents
                and not i.trashed
                and (not folders_only or i.mime_type == FOLDER_MIME)
            ]
            found.sort(key=lambda i: i.created, reverse=True)
            return [self._as_file(i) for i in found]

    def children_of(self, parent_ids: list[str]) -> DriveListing:
        if not parent_ids:
            return DriveListing()
        listed = {parent_id: self.children(parent_id) for parent_id in parent_ids}
        found = DriveListing(
            folders=[FolderItems(folder_id=p, items=listed[p]) for p in listed]
        )
        with self._state() as state:
            del state.calls[-len(parent_ids) :]
            state.calls.append("children_of")
        return found

    def find_folder(self, parent_id: str, name: str) -> DriveFolder | None:
        with self._state("find") as state:
            self._live(state, parent_id, f"Finding {name!r}")
            found = [
                i
                for i in state.items.values()
                if parent_id in i.parents
                and i.name == name
                and i.mime_type == FOLDER_MIME
                and not i.trashed
            ]
            if not found:
                return None
            oldest = min(found, key=lambda i: i.created)
            return DriveFolder(id=oldest.id, name=oldest.name)

    def create_folder(
        self, parent_id: str, name: str, *, owner: str = FAKE_BOT_EMAIL
    ) -> DriveFolder:
        with self._state("create") as state:
            if name in state.fail_on:
                msg = f"Creating {name!r}: Drive refused (fake outage)"
                raise DriveError(msg)
            self._live(state, parent_id, f"Creating {name!r}")
            made = self._new(state, name=name, parents=[parent_id], owner=owner)
            return DriveFolder(id=made.id, name=made.name)

    def ensure_folder(self, parent_id: str, name: str) -> DriveFolder:
        return self.find_folder(parent_id, name) or self.create_folder(parent_id, name)

    def rename(self, folder_id: str, name: str) -> DriveFolder:
        with self._state("rename") as state:
            item = self._live(state, folder_id, f"Renaming {folder_id} to {name!r}")
            item.name = name
            return DriveFolder(id=item.id, name=item.name)

    def file(self, file_id: str) -> DriveFile:
        with self._state("file") as state:
            return self._as_file(self._live(state, file_id, f"File {file_id}"))

    def find_file(self, parent_id: str, name: str) -> DriveFile | None:
        with self._state("find_file") as state:
            self._live(state, parent_id, f"Finding {name!r}")
            found = [
                i
                for i in state.items.values()
                if parent_id in i.parents
                and i.name == name
                and i.mime_type != FOLDER_MIME
                and not i.trashed
            ]
            if not found:
                return None
            return self._as_file(min(found, key=lambda i: i.created))

    def sheet(self, file_id: str) -> DriveSheet:
        with self._state("sheet") as state:
            item = self._live(state, file_id, f"Sheet {file_id}")
            if item.sheet is None:
                msg = f"Sheet {file_id}: Drive refused (Export only supports Docs Editors files.)"
                raise DriveError(msg)
            return item.sheet

    def storage(self) -> DriveStorage:
        with self._state("storage") as state:
            return DriveStorage(usage=state.storage_used, limit=state.storage_limit)

    def set_storage(self, limit: int | None, used: int = 0) -> None:
        with self._state() as state:
            state.storage_limit = limit
            state.storage_used = used

    def thumbnail(self, item: DriveFile, px: int) -> DriveThumb | None:  # noqa: ARG002
        with self._state("thumbnail"):
            if not item.thumbnail_link:
                return None
            return DriveThumb(content=_thumb_jpeg(), media_type="image/jpeg")

    def download(self, file_id: str) -> DriveDownload:
        item = self.file(file_id)
        return DriveDownload(content=b"", name=item.name)

    def upload(
        self,
        parent_id: str,
        path: Path,
        name: str,
        mime_type: str,
        *,
        owner: str = UPLOADER,
    ) -> DriveFile:
        size = path.stat().st_size
        with self._state("upload") as state:
            if name in state.fail_on:
                msg = f"Uploading {name!r}: Drive refused (fake outage)"
                raise DriveError(msg)
            limit = state.storage_limit
            if limit is not None and state.storage_used + size > limit:
                msg = (
                    f"Uploading {name!r}: Drive refused (The user's Drive storage"
                    " quota has been exceeded.)"
                )
                raise DriveFullError(msg)
            state.storage_used += size
            parent = self._live(state, parent_id, f"Uploading {name!r}")
            if parent.mime_type != FOLDER_MIME:
                msg = f"Uploading {name!r}: {parent_id} is not a folder"
                raise DriveError(msg)
            made = self._new(
                state,
                name=name,
                mime_type=mime_type,
                parents=[parent_id],
                owner=owner,
                size=size,
            )
            return self._as_file(made)

    def grant(self, item_id: str, email: str) -> None:
        with self._state("grant") as state:
            item = self._live(state, item_id, f"Sharing {item_id} with {email}")
            if email not in item.editors:
                item.editors.append(email)

    def seed_folder(
        self,
        name: str,
        parent: str | None = None,
        *,
        owner: str = DEFAULT_OWNER,
        item_id: str | None = None,
        shared: bool = False,
    ) -> str:
        with self._state() as state:
            if item_id is not None and item_id in state.items:
                return item_id
            made = self._new(
                state, name=name, parents=[parent] if parent else [], owner=owner
            )
            if item_id is not None:
                del state.items[made.id]
                made.id = item_id
                state.items[item_id] = made
            if shared:
                state.shared.append(made.id)
            return made.id

    def add_file(
        self,
        parent: str,
        name: str,
        mime_type: str = "image/jpeg",
        *,
        owner: str = DEFAULT_OWNER,
        thumbnail: bool = False,
        created: datetime | None = None,
    ) -> str:
        with self._state() as state:
            return self._new(
                state,
                name=name,
                mime_type=mime_type,
                parents=[parent],
                owner=owner,
                has_thumbnail=thumbnail,
                created_time=created or datetime.now(UTC),
            ).id

    def add_sheet(
        self,
        parent: str,
        name: str,
        tabs: list[SheetTab],
        *,
        owner: str = DEFAULT_OWNER,
    ) -> str:
        with self._state() as state:
            content = DriveSheet(tabs=tabs)
            for item in state.items.values():
                if parent in item.parents and item.name == name and item.sheet:
                    item.sheet = content
                    return item.id
            return self._new(
                state,
                name=name,
                mime_type=SHEET_MIME,
                parents=[parent],
                owner=owner,
                sheet=content,
            ).id

    def trash_file(self, item_id: str) -> None:
        with self._state("trash_file") as state:
            self._live(state, item_id, f"File {item_id}").trashed = True
            self._trash_below(state, item_id)

    @staticmethod
    def _trash_below(state: FakeDriveState, item_id: str) -> None:
        waiting = [item_id]
        while waiting:
            parent = waiting.pop()
            for item in state.items.values():
                if parent in item.parents and not item.trashed:
                    item.trashed = True
                    waiting.append(item.id)

    def trash(self, item_id: str) -> None:
        with self._state() as state:
            state.items[item_id].trashed = True
            self._trash_below(state, item_id)

    def delete(self, item_id: str) -> None:
        with self._state() as state:
            del state.items[item_id]

    def move(self, item_id: str, parent: str) -> None:
        with self._state() as state:
            state.items[item_id].parents = [parent]

    def rename_by_hand(self, item_id: str, name: str) -> None:
        with self._state() as state:
            state.items[item_id].name = name

    def share(self, item_id: str) -> None:
        with self._state() as state:
            if item_id not in state.shared:
                state.shared.append(item_id)

    def unshare(self, item_id: str) -> None:
        with self._state() as state:
            state.shared = [i for i in state.shared if i != item_id]

    def fail_on(self, *names: str) -> None:
        with self._state() as state:
            state.fail_on = list(names)

    def refuse_sign_in(self, email: str) -> None:
        with self._state() as state:
            if email not in state.refused:
                state.refused.append(email)

    def check_sign_in(self, email: str) -> None:
        with self._state() as state:
            if email in state.refused:
                raise DriveAuthError

    def named(self, name: str, parent: str) -> list[str]:
        with self._state() as state:
            found = [
                i
                for i in state.items.values()
                if i.name == name and parent in i.parents and not i.trashed
            ]
            return [i.id for i in sorted(found, key=lambda i: i.created)]

    def names_in(self, parent: str) -> list[str]:
        with self._state() as state:
            found = [
                i for i in state.items.values() if parent in i.parents and not i.trashed
            ]
            return [i.name for i in sorted(found, key=lambda i: i.created)]

    def item(self, item_id: str) -> FakeItem | None:
        with self._state() as state:
            found = state.items.get(item_id)
            return found.model_copy() if found else None

    @property
    def calls(self) -> list[str]:
        with self._state() as state:
            return list(state.calls)

    @property
    def writes(self) -> list[str]:
        return [c for c in self.calls if c in _WRITES]

    def clear_calls(self) -> None:
        with self._state() as state:
            state.calls.clear()


@lru_cache(maxsize=4)
def _fake_drive(state_dir: str, root_id: str) -> FakeDrive:
    drive = FakeDrive(state_dir or None)
    if root_id:
        drive.seed_folder("ALL ACCOUNTS", item_id=root_id, shared=True)
    return drive


def fake_drive() -> FakeDrive:
    return _fake_drive(settings.infra.fakes_dir, settings.gdrive.root_folder_id)


class FakePersonDrive:
    def __init__(self, drive: FakeDrive, email: str) -> None:
        self._drive = drive
        self.email = email
        self.my_drive = f"mydrive-{email}"

    def _me(self) -> FakeDrive:
        self._drive.check_sign_in(self.email)
        return self._drive

    def _id(self, item_id: str) -> str:
        if item_id != "root":
            return item_id
        if self._drive.item(self.my_drive) is None:
            self._drive.seed_folder(
                "My Drive", item_id=self.my_drive, owner=self.email, shared=True
            )
        return self.my_drive

    def folder(self, folder_id: str) -> DriveFolder:
        return self._me().folder(self._id(folder_id))

    def children(
        self, parent_id: str, *, folders_only: bool = False
    ) -> list[DriveFile]:
        return self._me().children(self._id(parent_id), folders_only=folders_only)

    def children_of(self, parent_ids: list[str]) -> DriveListing:
        return self._me().children_of([self._id(p) for p in parent_ids])

    def find_folder(self, parent_id: str, name: str) -> DriveFolder | None:
        return self._me().find_folder(self._id(parent_id), name)

    def create_folder(self, parent_id: str, name: str) -> DriveFolder:
        return self._me().create_folder(self._id(parent_id), name, owner=self.email)

    def ensure_folder(self, parent_id: str, name: str) -> DriveFolder:
        return self.find_folder(parent_id, name) or self.create_folder(parent_id, name)

    def rename(self, folder_id: str, name: str) -> DriveFolder:
        return self._me().rename(folder_id, name)

    def file(self, file_id: str) -> DriveFile:
        return self._me().file(file_id)

    def find_file(self, parent_id: str, name: str) -> DriveFile | None:
        return self._me().find_file(self._id(parent_id), name)

    def sheet(self, file_id: str) -> DriveSheet:
        return self._me().sheet(file_id)

    def thumbnail(self, item: DriveFile, px: int) -> DriveThumb | None:
        return self._me().thumbnail(item, px)

    def download(self, file_id: str) -> DriveDownload:
        return self._me().download(self._id(file_id))

    def storage(self) -> DriveStorage:
        return self._me().storage()

    def upload(
        self, parent_id: str, path: Path, name: str, mime_type: str
    ) -> DriveFile:
        return self._me().upload(
            self._id(parent_id), path, name, mime_type, owner=self.email
        )

    def grant(self, item_id: str, email: str) -> None:
        self._me().grant(item_id, email)

    def trash_file(self, item_id: str) -> None:
        self._me().trash_file(item_id)


def fake_person(refresh_token: str) -> FakePersonDrive:
    return FakePersonDrive(fake_drive(), refresh_token.removeprefix("fake-refresh:"))
