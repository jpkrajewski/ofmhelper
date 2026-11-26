# Copyright (C) 2026 jpkrajewski

from contextlib import contextmanager
lazy from collections.abc import Iterator

from pydantic import BaseModel, SkipValidation
from redis.exceptions import LockError, RedisError

from ofmhelpers.cache import get_redis, get_text, set_text
from ofmhelpers.config import settings
from ofmhelpers.features.storage.constants import StorageConstants
from ofmhelpers.features.storage.folders import StorageServiceFolders
from ofmhelpers.features.storage.models import ReviewPlace
from ofmhelpers.integrations.storage import (
    DriveAuthError,
    FolderGoneError,
    ScopedDrive,
    StorageClient,
)
from ofmhelpers.log import get_logger
lazy from ofmhelpers.config.types import AccountFolder

logger = get_logger(__name__)


@contextmanager
def _review_lock(user_id: int) -> Iterator[None]:
    wait_s = settings.gdrive.review_lock_s
    lock = get_redis().lock(
        f"lock:drive-review:{user_id}",
        timeout=wait_s,
        blocking_timeout=wait_s,
    )
    try:
        held = bool(lock.acquire())
    except RedisError:
        logger.warning("drive: review lock of %s unavailable", user_id, exc_info=True)
        held = False
    else:
        if not held:
            logger.warning(
                "drive: review lock of %s not had in %s s: going ahead",
                user_id,
                wait_s,
            )
    try:
        yield
    finally:
        if held:
            try:
                lock.release()
            except RedisError, LockError:
                logger.warning(
                    "drive: review lock of %s not released",
                    user_id,
                    exc_info=True,
                )


def _folder_key(user_id: int, root: str, name: str) -> str:
    return f"drive:review-folder:{user_id}:{root}:{name}"


def _remembered(root: ReviewPlace, key: str, name: str) -> str | None:
    known = get_text(key)
    if not known:
        return None
    try:
        current = root.drive.folder(known)
    except FolderGoneError:  # silent: deleted by hand: found again
        logger.info(
            "drive: review folder %s (%s) is gone: finding it again",
            name,
            known,
        )
        return None
    if current.name != name or root.folder_id not in current.parents:
        return None
    return known


def _review_folder_id(root: ReviewPlace, user_id: int, name: str) -> str:
    key = _folder_key(user_id, root.folder_id, name)
    known = _remembered(root, key, name)
    if known:
        return known
    drive = root.drive
    found = drive.find_folder(root.folder_id, name)
    if found is None:
        made = drive.create_folder(root.folder_id, name)
        found = drive.find_folder(root.folder_id, name) or made
    set_text(key, found.id, settings.gdrive.review_folder_ttl_s)
    return found.id


class StorageServiceReview(BaseModel, frozen=True, arbitrary_types_allowed=True):
    client: SkipValidation[StorageClient] | None
    owner_id: int
    read_live: bool
    constants: StorageConstants
    folders: StorageServiceFolders

    def _drive(self) -> StorageClient:
        if self.client is None:
            raise DriveAuthError
        return self.client

    def account_subfolder(
        self,
        place: ReviewPlace,
        account_folder_id: str,
        name: AccountFolder,
    ) -> str:
        drive = place.drive
        if not self.read_live:
            return drive.ensure_folder(account_folder_id, name).id
        account = drive.folder(account_folder_id)
        twin = drive.ensure_folder(place.folder_id, account.name).id
        return drive.ensure_folder(twin, name).id

    def target(self) -> ReviewPlace:
        drive = self._drive()
        if not self.read_live:
            return ReviewPlace(drive=drive, folder_id=self.folders.ensure_root())
        c = self.constants
        name = f"{c.dev_prefix}{c.root_name}"
        named = [
            f
            for f in drive.children("root", folders_only=True)
            if f.name.strip().casefold() == name.casefold()
        ]
        root = named[-1].id if named else drive.create_folder("root", name).id
        return ReviewPlace(drive=ScopedDrive(drive, root), folder_id=root)

    def folder(self, name: str) -> ReviewPlace:
        with _review_lock(self.owner_id):
            root = self.target()
            return ReviewPlace(
                drive=root.drive,
                folder_id=_review_folder_id(root, self.owner_id, name),
            )
