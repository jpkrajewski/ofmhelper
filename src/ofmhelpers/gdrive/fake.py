"""An in-process Google Drive (`DriveApi`) for tests and OFM_FAKE_EXTERNALS.

It behaves the way the real one does where /drive depends on it
(tests/contract/test_drive_contract.py runs the same checks on both):

- names are not unique: `create_folder` always makes a new item;
- every item has parents; `children` lists newest first;
- trash is not delete: a trashed item (or one under a trashed folder) is
  hidden but still exists, a deleted one is gone;
- the account sees only what is shared with it (an item or a folder above
  it), anything else answers like a 404 (`FolderGoneError`);
- each item has an owner; the bot owns what it creates;
- previews exist only for items that have one (a fresh video has none).

State lives in memory, or, with `OFM_FAKES_DIR` set, in `drive.json` there
under an exclusive file lock, so the app under test and the test seeding
it share one Drive across processes. `trash`, `delete`, `add_file`,
`seed_folder`, `move`, `share`/`unshare`, `fail_on` stand in for what a person
(or an outage) does in Drive.
"""

from __future__ import annotations

import fcntl
import io
import threading
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

from PIL import Image
from pydantic import BaseModel, Field

from ofmhelpers.config import settings
from ofmhelpers.gdrive.base import DriveError, FolderGoneError
from ofmhelpers.gdrive.models import FOLDER_MIME, DriveFile, DriveFolder

if TYPE_CHECKING:
    from collections.abc import Iterator

FAKE_BOT_EMAIL = "drive-bot@fake.iam.gserviceaccount.com"
DEFAULT_OWNER = "owner@example.com"
_STATE_FILE = "drive.json"


class FakeItem(BaseModel):
    id: str
    name: str
    mime_type: str = FOLDER_MIME
    parents: list[str] = Field(default_factory=list)
    owner: str = DEFAULT_OWNER
    # Order of creation: Drive's createdTime, without a clock.
    created: int = 0
    trashed: bool = False
    has_thumbnail: bool = False
    # Who else may edit it (`grant`), besides the bot.
    editors: list[str] = Field(default_factory=list)


class FakeDriveState(BaseModel):
    items: dict[str, FakeItem] = Field(default_factory=dict)
    # Items shared with the bot (it sees them and all below).
    shared: list[str] = Field(default_factory=list)
    next_id: int = 1
    # Every call, in order ("create", "rename", ...): a test tells a
    # sweep that did nothing from one that wrote.
    calls: list[str] = Field(default_factory=list)
    # Folder names whose creation Drive refuses (an outage, a quota).
    fail_on: list[str] = Field(default_factory=list)


_WRITES = frozenset({"create", "rename", "upload", "grant"})
# A person's uploads, as `connect_as` makes them.
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

    # --- state ------------------------------------------------------------

    @contextmanager
    def _state(self, call: str | None = None) -> Iterator[FakeDriveState]:
        """The state, locked (thread and, with a file, process); saved
        when the block ends."""
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
        """The item and every folder above it (None for a deleted one)."""
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
        """The item if the bot can see it: it exists, nothing on its way up
        is trashed or deleted, and it or a folder above it is shared."""
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
        # Drive's own field names, as the real client gets them.
        return DriveFile.model_validate(
            {
                "id": item.id,
                "name": item.name,
                "mimeType": item.mime_type,
                "hasThumbnail": item.has_thumbnail,
                "parents": list(item.parents),
                "thumbnailLink": (
                    f"fake://thumb/{item.id}=s220" if item.has_thumbnail else ""
                ),
                "ownedByMe": item.owner == FAKE_BOT_EMAIL,
            }
        )

    # --- DriveApi -----------------------------------------------------------

    def folder(self, folder_id: str) -> DriveFolder:
        with self._state("folder") as state:
            item = self._live(state, folder_id, f"Folder {folder_id}")
            if item.mime_type != FOLDER_MIME:
                msg = f"Folder {folder_id} is trashed or not a folder"
                raise FolderGoneError(msg)
            return DriveFolder(id=item.id, name=item.name, parents=list(item.parents))

    def children(
        self, parent_id: str, *, folders_only: bool = False
    ) -> list[DriveFile]:
        with self._state("children") as state:
            # As Drive (checked live 2026-10-21): a missing or unshared parent
            # is a 404, but a TRASHED one still lists its children -- so a
            # listing never tells a deleted folder; callers check `folder`.
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

    def children_of(self, parent_ids: list[str]) -> dict[str, list[DriveFile]]:
        found: dict[str, list[DriveFile]] = {}
        if not parent_ids:
            return found
        for parent_id in parent_ids:
            found[parent_id] = self.children(parent_id)
        with self._state() as state:
            # One query, as the real one: the per-parent listings above are
            # an implementation detail, not calls a test should count.
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

    def thumbnail(self, item: DriveFile, px: int) -> tuple[bytes, str] | None:  # noqa: ARG002 -- one size fits every px
        with self._state("thumbnail"):
            if not item.thumbnail_link:
                return None
            return _thumb_jpeg(), "image/jpeg"

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
            )
            found = self._as_file(made)
        return found.model_copy(update={"size": size})

    def grant(self, item_id: str, email: str) -> None:
        with self._state("grant") as state:
            item = self._live(state, item_id, f"Sharing {item_id} with {email}")
            if email not in item.editors:
                item.editors.append(email)

    # --- what a person does in Drive (tests, seeding) ------------------------

    def seed_folder(
        self,
        name: str,
        parent: str | None = None,
        *,
        owner: str = DEFAULT_OWNER,
        item_id: str | None = None,
        shared: bool = False,
    ) -> str:
        """A folder made by hand (`item_id` fixes its id: a configured
        root). `shared` shares it with the bot."""
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
    ) -> str:
        with self._state() as state:
            return self._new(
                state,
                name=name,
                mime_type=mime_type,
                parents=[parent],
                owner=owner,
                has_thumbnail=thumbnail,
            ).id

    def trash_file(self, item_id: str) -> None:
        """The API call (the app trashing what it uploaded)."""
        with self._state("trash_file") as state:
            self._live(state, item_id, f"File {item_id}").trashed = True

    def trash(self, item_id: str) -> None:
        """Trashed by hand in Drive (a test's doing, not the app's)."""
        with self._state() as state:
            state.items[item_id].trashed = True

    def delete(self, item_id: str) -> None:
        """Gone for good (emptied trash): everything under it with it."""
        with self._state() as state:
            del state.items[item_id]

    def move(self, item_id: str, parent: str) -> None:
        """Dragged into another folder (a VA moving a posted video)."""
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

    # --- what a test reads back ---------------------------------------------

    def named(self, name: str, parent: str) -> list[str]:
        """Ids of the live items called `name` in `parent`, oldest first."""
        with self._state() as state:
            found = [
                i
                for i in state.items.values()
                if i.name == name and parent in i.parents and not i.trashed
            ]
            return [i.id for i in sorted(found, key=lambda i: i.created)]

    def names_in(self, parent: str) -> list[str]:
        """Names of the live items directly in `parent`, oldest first."""
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
    """The process's fake Drive (`connect()` under OFM_FAKE_EXTERNALS), its
    root the configured one, shared with the bot."""
    return _fake_drive(settings.infra.fakes_dir, settings.gdrive.root_folder_id)


class FakePersonDrive:
    """The fake Drive as one person (`connect_as` under OFM_FAKE_EXTERNALS):
    "root" is their own My Drive (a folder of theirs, made on first use),
    and what they make or upload is theirs. Everything else is the shared
    fake's, so a test reads back through `FakeDrive` as usual."""

    def __init__(self, drive: FakeDrive, email: str) -> None:
        self._drive = drive
        self.email = email
        self.my_drive = f"mydrive-{email}"

    def _id(self, item_id: str) -> str:
        if item_id != "root":
            return item_id
        if self._drive.item(self.my_drive) is None:
            self._drive.seed_folder(
                "My Drive", item_id=self.my_drive, owner=self.email, shared=True
            )
        return self.my_drive

    def folder(self, folder_id: str) -> DriveFolder:
        return self._drive.folder(self._id(folder_id))

    def children(
        self, parent_id: str, *, folders_only: bool = False
    ) -> list[DriveFile]:
        return self._drive.children(self._id(parent_id), folders_only=folders_only)

    def children_of(self, parent_ids: list[str]) -> dict[str, list[DriveFile]]:
        return self._drive.children_of([self._id(p) for p in parent_ids])

    def find_folder(self, parent_id: str, name: str) -> DriveFolder | None:
        return self._drive.find_folder(self._id(parent_id), name)

    def create_folder(self, parent_id: str, name: str) -> DriveFolder:
        return self._drive.create_folder(self._id(parent_id), name, owner=self.email)

    def ensure_folder(self, parent_id: str, name: str) -> DriveFolder:
        return self.find_folder(parent_id, name) or self.create_folder(parent_id, name)

    def rename(self, folder_id: str, name: str) -> DriveFolder:
        return self._drive.rename(folder_id, name)

    def file(self, file_id: str) -> DriveFile:
        return self._drive.file(file_id)

    def thumbnail(self, item: DriveFile, px: int) -> tuple[bytes, str] | None:
        return self._drive.thumbnail(item, px)

    def upload(
        self, parent_id: str, path: Path, name: str, mime_type: str
    ) -> DriveFile:
        return self._drive.upload(
            self._id(parent_id), path, name, mime_type, owner=self.email
        )

    def grant(self, item_id: str, email: str) -> None:
        self._drive.grant(item_id, email)

    def trash_file(self, item_id: str) -> None:
        self._drive.trash_file(item_id)


def fake_person(refresh_token: str) -> FakePersonDrive:
    """The fake Drive as whoever `refresh_token` is ("fake-refresh:<email>",
    what the fake token endpoint hands out; any other token is its own
    person)."""
    return FakePersonDrive(fake_drive(), refresh_token.removeprefix("fake-refresh:"))
