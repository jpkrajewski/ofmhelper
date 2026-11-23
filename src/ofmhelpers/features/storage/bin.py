# Copyright (C) 2026 jpkrajewski

import time

from pydantic import BaseModel, SkipValidation
from redis.exceptions import RedisError

from ofmhelpers.cache.lock import lock_held, locked
from ofmhelpers.config import settings
from ofmhelpers.db.drive_folders import store as drive_store
from ofmhelpers.db.jobs.schema import Job, JobStatus
from ofmhelpers.db.jobs.store import latest_job
from ofmhelpers.features.jobs.runner import start_job
from ofmhelpers.features.refusals import (
    ConflictError,
    NotFoundError,
    RefusedError,
    TryAgainError,
)
from ofmhelpers.features.storage.constants import StorageConstants, StorageTask
from ofmhelpers.features.storage.folders import StorageServiceFolders
from ofmhelpers.features.storage.models import (
    BinMove,
    BinPart,
    FolderUsage,
    Moved,
    Sizes,
    StoragePage,
    StorageUsage,
    _Place,
)
from ofmhelpers.features.storage.schema import MoveStarted
from ofmhelpers.integrations.storage import FolderGoneError, StorageClient
from ofmhelpers.log import get_logger
lazy from ofmhelpers.features.storage.jobs import run_bin, run_scan

logger = get_logger(__name__)


def _running(job: Job | None) -> bool:
    return job is not None and job.status is JobStatus.running


def _ended(job: Job | None, status: JobStatus) -> bool:
    return job is not None and job.status is status


class StorageServiceBin(BaseModel, frozen=True, arbitrary_types_allowed=True):
    user_id: int
    client: SkipValidation[StorageClient] | None
    read_live: bool
    constants: StorageConstants
    folders: StorageServiceFolders

    @property
    def actor(self) -> str:
        return str(self.user_id)

    def connected(self) -> bool:
        return self.client is not None

    def _drive(self) -> StorageClient:
        if self.client is None:
            raise RefusedError(self.constants.no_drive)
        return self.client

    def owner_folders(self) -> list[FolderUsage]:
        stored = drive_store.account_folders()
        found: list[FolderUsage] = []
        for target in drive_store.targets(self.user_id):
            folder = stored.get(target.login_id)
            if folder is None:
                continue
            found.append(
                FolderUsage(
                    login_id=target.login_id,
                    folder_id=folder.folder_id,
                    name=target.folder_name,
                ),
            )
        return found

    def _stale(self, scan: Job | None, move: Job | None) -> bool:
        if scan is None:
            return True
        if _running(scan):
            return False
        if move is not None and (move.finished_at or 0) > scan.created_at:
            return True
        return time.time() - scan.created_at > settings.storage.scan_stale_s

    def _start_scan(self) -> None:
        task = StorageTask.SCAN
        with locked(f"storage:scan:{self.user_id}", settings.storage.start_lock_s):
            if _running(latest_job(task, self.actor)):
                return
            start_job(
                task,
                {},
                run_scan,
                {"user_id": self.user_id},
                actor=self.actor,
                job_timeout=settings.storage.scan_timeout_s,
            )

    def _usage(self, scan: Job | None) -> StorageUsage | None:
        done = (
            scan
            if _ended(scan, JobStatus.done)
            else latest_job(StorageTask.SCAN, self.actor, JobStatus.done)
        )
        return StorageUsage.model_validate(done.result) if done else None

    def page(self) -> StoragePage:
        connected = self.connected()
        scan = latest_job(StorageTask.SCAN, self.actor)
        move = latest_job(StorageTask.BIN, self.actor)
        if connected and self._stale(scan, move):
            try:
                self._start_scan()
            except RedisError:
                logger.warning(
                    "storage: scan of %s not queued",
                    self.user_id,
                    exc_info=True,
                )
            scan = latest_job(StorageTask.SCAN, self.actor)
        return StoragePage(
            connected=connected,
            usage=self._usage(scan),
            scanning=_running(scan),
            scan_failed=_ended(scan, JobStatus.failed),
            moving=BinMove.model_validate(move.params)
            if move and _running(move)
            else None,
            moved=Moved.model_validate(move.result)
            if move and _ended(move, JobStatus.done)
            else None,
            move_failed=_ended(move, JobStatus.failed),
            bin_url=self.constants.bin_url,
        )

    def start_move(self, part: BinPart, folder_id: str | None) -> MoveStarted:
        if not self.connected():
            raise RefusedError(self.constants.no_drive)
        if self.read_live:
            raise RefusedError(self.constants.read_only)
        if folder_id is not None and folder_id not in {
            folder.folder_id for folder in self.owner_folders()
        }:
            raise NotFoundError(self.constants.no_folder)
        params = BinMove(part=part, folder_id=folder_id).model_dump(mode="json")
        with lock_held(
            f"storage:bin:{self.user_id}",
            settings.storage.start_lock_s,
        ) as held:
            if not held:
                raise TryAgainError(self.constants.try_again)
            if _running(latest_job(StorageTask.BIN, self.actor)):
                raise ConflictError(self.constants.busy)
            job_id = start_job(
                StorageTask.BIN,
                params,
                run_bin,
                {"user_id": self.user_id, **params},
                actor=self.actor,
                job_timeout=settings.storage.clean_timeout_s,
            )
        return MoveStarted(job_id=job_id)

    def _accounts(self) -> list[FolderUsage]:
        drive = self._drive()
        root = self.folders.find_root()
        if root is None:
            return []
        listed = {f.id for f in drive.children(root, folders_only=True)}
        return [f for f in self.owner_folders() if f.folder_id in listed]

    def _measure(self, tops: dict[str, Sizes], split: bool) -> None:
        drive = self._drive()
        places = {key: _Place(sizes=sizes, top=split) for key, sizes in tops.items()}
        seen = set(places)
        while places:
            listing = drive.children_of(list(places))
            below: dict[str, _Place] = {}
            for folder_id, place in places.items():
                for item in listing.of(folder_id):
                    if not item.is_folder:
                        place.sizes.add(item.size or 0, place.part)
                    elif item.id not in seen:
                        seen.add(item.id)
                        below[item.id] = place.below(item)
            places = below

    def scan(self) -> StorageUsage:
        folders = self._accounts()
        self._measure({f.folder_id: f.sizes for f in folders}, split=True)
        return StorageUsage(
            quota=self._drive().storage(),
            folders=folders,
            scanned_at=time.time(),
        )

    def _trash(self, item_id: str) -> bool:
        try:
            self._drive().trash_file(item_id)
        except FolderGoneError:
            logger.info("storage: %s is gone already", item_id, exc_info=True)
            return False
        return True

    def _move_part(self, folder: FolderUsage, part: BinPart, moved: Moved) -> None:
        drive = self._drive()
        found = drive.find_folder(folder.folder_id, part)
        if found is None:
            return
        items = drive.children(found.id)
        inside = {item.id: Sizes() for item in items if item.is_folder}
        self._measure(inside, split=False)
        for item in items:
            if self._trash(item.id):
                moved.items += 1
                moved.bytes += (
                    inside[item.id].total if item.is_folder else item.size or 0
                )
        logger.info(
            "storage: %s of %s moved to the bin (%s items so far)",
            part,
            folder.name,
            moved.items,
        )

    def move(self, part: BinPart, folder_id: str | None) -> Moved:
        folders = [f for f in self._accounts() if folder_id in {None, f.folder_id}]
        if folder_id is not None and not folders:
            logger.warning(
                "storage: folder %s is no longer in the Drive root: nothing moved",
                folder_id,
            )
        moved = Moved(part=part, folder_id=folder_id)
        for folder in folders:
            self._move_part(folder, part, moved)
        return moved
