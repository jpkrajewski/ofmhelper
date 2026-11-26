# Copyright (C) 2026 jpkrajewski

from pydantic import BaseModel, SkipValidation

from ofmhelpers.db.drive_folders import store
from ofmhelpers.db.drive_folders.schema import SyncReport, VaFolder
from ofmhelpers.db.users import store as users
from ofmhelpers.integrations.signin import SignInError
from ofmhelpers.integrations.storage import (
    DriveAuthError,
    DriveError,
    FolderGoneError,
    StorageClient,
)
from ofmhelpers.log import get_logger
lazy from ofmhelpers.db.users.schema import User

logger = get_logger(__name__)


class StorageServiceVaFolders(BaseModel, frozen=True, arbitrary_types_allowed=True):
    client: SkipValidation[StorageClient] | None
    owner_id: int
    read_live: bool

    def _drive(self) -> StorageClient:
        if self.client is None:
            raise DriveAuthError
        return self.client

    def active(self) -> list[User]:
        found = [va for va in users.vas_of(self.owner_id) if va.active]
        return sorted(found, key=lambda va: va.label.lower())

    def of_owner(self) -> list[VaFolder]:
        folders = store.va_folders(self.active(), any_root=self.read_live)
        return sorted(folders, key=lambda f: f.name.lower())

    def ensure(self, root: str, va: User, stored: VaFolder | None) -> VaFolder | None:
        drive = self._drive()
        name = va.label
        if stored is not None:
            try:
                current = drive.folder(stored.folder_id)
            except FolderGoneError:
                logger.warning(
                    "drive: folder of VA %s is gone: made again",
                    va.id,
                    exc_info=True,
                )
            else:
                if current.name != name:
                    drive.rename(current.id, name)
                folder = VaFolder(
                    user_id=va.id,
                    folder_id=current.id,
                    name=name,
                    root_folder_id=root,
                )
                if stored != folder and not store.save_va_folder(folder):
                    logger.info("drive: VA %s gone meanwhile, skipped", va.id)
                    return None
                return folder
        found = drive.find_folder(root, name) or drive.create_folder(root, name)
        folder = VaFolder(
            user_id=va.id,
            folder_id=found.id,
            name=name,
            root_folder_id=root,
        )
        if not store.save_va_folder(folder):
            logger.info("drive: VA %s gone meanwhile, folder left as it is", va.id)
            return None
        logger.info("drive: folder of VA %s is %s (%s)", va.id, found.id, name)
        return folder

    def sync(self, root: str, report: SyncReport) -> None:
        vas = self.active()
        stored = {f.user_id: f for f in store.va_folders(vas)}
        for va in vas:
            folder = stored.get(va.id)
            if folder is not None and folder.name == va.label:
                continue
            try:
                kept = self.ensure(root, va, folder)
            except (DriveError, SignInError) as exc:
                logger.warning("drive: folder of VA %s failed", va.id, exc_info=True)
                report.failed.append(f"{va.label}: {exc}")
                continue
            if kept is not None:
                made = report.renamed if folder is not None else report.created
                made.append(va.label)
