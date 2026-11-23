# Copyright (C) 2026 jpkrajewski

import mimetypes
from pathlib import Path

from pydantic import BaseModel, SkipValidation

from ofmhelpers.cache import enqueue
from ofmhelpers.config.types import AccountFolder
from ofmhelpers.db.drive_folders import store as drive_folders
from ofmhelpers.db.drive_folders.models import UploadTarget
from ofmhelpers.db.drive_folders.schema import (
    DriveUpload,
    StoredFolder,
    UploadsQueued,
    VaFolder,
)
from ofmhelpers.db.drive_uploads import store
from ofmhelpers.db.jobs.store import Readers, create_job, fail_job, get_job
from ofmhelpers.db.tracked_accounts.store import list_accounts
from ofmhelpers.features.assets.names import download_name
from ofmhelpers.features.jobs.runner import run_job
from ofmhelpers.features.refusals import RefusedError, UnavailableError
from ofmhelpers.features.storage.constants import StorageConstants, StorageTask
from ofmhelpers.features.storage.folders import StorageServiceFolders
from ofmhelpers.features.storage.models import ReviewPlace
from ofmhelpers.features.storage.review import StorageServiceReview
from ofmhelpers.features.storage.schema import _Creators, _UploadFile
from ofmhelpers.features.storage.va_folders import StorageServiceVaFolders
from ofmhelpers.integrations.signin import SignInError
from ofmhelpers.integrations.socials import get_platform
from ofmhelpers.integrations.storage import (
    DriveError,
    DriveNotSetUpError,
    FolderGoneError,
    StorageClient,
)
from ofmhelpers.log import get_logger
lazy from ofmhelpers.features.storage.jobs import upload_files

type Folder = StoredFolder | VaFolder

logger = get_logger(__name__)


def _files(job_id: str, readers: Readers) -> list[_UploadFile]:
    job = get_job(job_id, readers)
    if job is None or job.status != "done":
        return []
    return [
        _UploadFile(index=index, path=Path(raw))
        for index, raw in enumerate(job.local_paths())
        if raw is not None and Path(raw).is_file()
    ]


class StorageServiceUploads(BaseModel, frozen=True, arbitrary_types_allowed=True):
    client: SkipValidation[StorageClient] | None
    user_id: int
    constants: StorageConstants
    folders: StorageServiceFolders
    review: StorageServiceReview
    va_folders: StorageServiceVaFolders

    @property
    def actor(self) -> str:
        return str(self.user_id)

    def connected(self) -> bool:
        return self.client is not None

    def _creators(self) -> _Creators:
        return _Creators(
            by_handle={
                a.handle: a.creator
                for a in list_accounts(self.user_id)
                if a.platform == self.constants.upload_platform and a.creator
            },
        )

    def _handle(self, username: str) -> str:
        try:
            return get_platform(self.constants.upload_platform).handle(username)
        except KeyError, ValueError:  # silent: not a handle: kept as typed
            return username

    def _enqueue(self, upload_ids: list[int]) -> None:
        job_id = create_job(
            StorageTask.UPLOAD,
            {"upload_ids": upload_ids},
            actor=self.actor,
        )
        try:
            enqueue(run_job, (job_id, upload_files, {"upload_ids": upload_ids}))
        except Exception as exc:
            logger.warning("drive uploads %s not queued", upload_ids, exc_info=True)
            fail_job(job_id, self.constants.upload_not_queued)
            for upload_id in upload_ids:
                store.finish(
                    upload_id,
                    error=self.constants.upload_not_queued_why.format(reason=exc),
                )

    def targets(self) -> list[UploadTarget]:
        folders = drive_folders.account_folders()
        creators = self._creators()
        found = []
        for t in drive_folders.targets(self.user_id):
            folder = folders.get(t.login_id)
            if folder is None:
                continue
            found.append(
                UploadTarget(
                    login_id=t.login_id,
                    folder_name=t.folder_name,
                    handle=t.handle,
                    owner=t.owner,
                    creator=creators.of(self._handle(t.handle)),
                    waiting=len(folder.scan.waiting) if folder.scan else None,
                    followers=t.followers,
                ),
            )
        return found

    def _va_targets(self, va_ids: list[int]) -> list[VaFolder]:
        theirs = {f.user_id: f for f in self.va_folders.of_owner()}
        if not set(va_ids) <= set(theirs):
            msg = self.constants.va_no_folder.format(page=self.constants.drive_page)
            raise RefusedError(msg)
        return [theirs[va] for va in dict.fromkeys(va_ids)]

    def _queued(
        self,
        job_ids: list[str],
        login_ids: list[int],
        readers: Readers,
        va_ids: list[int],
    ) -> list[DriveUpload]:
        if not login_ids and not va_ids:
            msg = self.constants.pick_folder
            raise RefusedError(msg)
        mine = {t.login_id for t in drive_folders.targets(self.user_id)}
        if (va_ids or mine & set(login_ids)) and self.client is None:
            msg = self.constants.connect_first
            raise RefusedError(msg)
        usable = {t.login_id: t for t in self.targets()}
        if not set(login_ids) <= mine or not set(login_ids) <= set(usable):
            msg = self.constants.account_no_folder.format(
                page=self.constants.drive_page,
            )
            raise RefusedError(msg)
        vas = self._va_targets(va_ids)
        files = [
            (job, index)
            for job in job_ids
            for index in (f.index for f in _files(job, readers))
        ]
        ids = [
            upload_id
            for login_id in dict.fromkeys(login_ids)
            for job_id, index in files
            if (
                upload_id := store.queue(
                    job_id,
                    index,
                    login_id,
                    self.user_id,
                    usable[login_id].folder_name,
                )
            )
            is not None
        ] + [
            upload_id
            for folder in vas
            for job_id, index in files
            if (
                upload_id := store.queue(
                    job_id,
                    index,
                    None,
                    self.user_id,
                    folder.name,
                    va_id=folder.user_id,
                )
            )
            is not None
        ]
        if ids:
            self._enqueue(ids)
        return store.for_jobs(
            [j for j in job_ids if get_job(j, readers) is not None],
            frozenset({self.user_id}),
        )

    def queue(
        self,
        job_ids: list[str],
        login_ids: list[int],
        readers: Readers = None,
        va_ids: list[int] | None = None,
    ) -> UploadsQueued:
        readers = readers if readers is not None else self.actor
        uploads = self._queued(job_ids, login_ids, readers, va_ids or [])
        return UploadsQueued(
            queued=sum(1 for u in uploads if u.status == "queued"),
            uploads=uploads,
        )

    def remove(self, upload_id: int, readers: Readers = None) -> DriveUpload:
        found = store.get(upload_id)
        readers = readers if readers is not None else self.actor
        if (
            found is None
            or store.uploader(upload_id) != self.user_id
            or get_job(found.job_id, readers) is None
        ):
            msg = self.constants.no_such_upload
            raise RefusedError(msg)
        if found.status != "done":
            msg = self.constants.only_done_removable
            raise RefusedError(msg)
        file_id = store.file_id(upload_id)
        if not file_id or self.client is None:
            msg = self.constants.connect_first
            raise RefusedError(msg)
        try:
            self.client.trash_file(file_id)
        except FolderGoneError:  # silent: already gone in Drive
            logger.info("drive upload %s was already gone in Drive", upload_id)
        except (SignInError, DriveNotSetUpError) as exc:
            logger.warning(
                "drive upload %s: sign-in refused: %s",
                upload_id,
                exc,
                exc_info=True,
            )
            msg = self.constants.connect_first
            raise RefusedError(msg) from exc
        except DriveError as exc:
            logger.warning("drive upload %s: not removed", upload_id, exc_info=True)
            msg = self.constants.remove_failed
            raise UnavailableError(msg) from exc
        store.remove(upload_id)
        logger.info("drive upload %s removed from %s", upload_id, found.folder_name)
        return found.model_copy(update={"status": "removed"})

    def _destination(
        self,
        upload: DriveUpload,
        folder: Folder,
        drive: StorageClient,
    ) -> ReviewPlace:
        job = get_job(upload.job_id, None)
        if (
            job is None
            or job.task != StorageTask.SPOOFED
            or isinstance(folder, VaFolder)
        ):
            return ReviewPlace(drive=drive, folder_id=folder.folder_id)
        place = self.review.target()
        spoofed = self.review.account_subfolder(
            place,
            folder.folder_id,
            AccountFolder.SPOOFED,
        )
        return ReviewPlace(drive=place.drive, folder_id=spoofed)

    def upload(self, upload: DriveUpload, folder: Folder) -> None:
        files = {f.index: f.path for f in _files(upload.job_id, None)}
        path = files.get(upload.file_index)
        if path is None:
            store.finish(upload.id, error=self.constants.file_missing)
            return
        drive = self.client
        if drive is None:
            store.finish(upload.id, error=self.constants.owner_disconnected)
            return
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        try:
            root = self.folders.usable_root()
            if root is None or folder.root_folder_id != root:
                logger.warning(
                    "drive upload %s refused: folder %s not in %s's root %s",
                    upload.id,
                    folder.folder_id,
                    self.user_id,
                    root,
                )
                store.finish(
                    upload.id,
                    error=self.constants.folder_outside_root.format(
                        root=self.folders.root_name(),
                        page=self.constants.drive_page,
                    ),
                )
                return
            target = self._destination(upload, folder, drive)
            made = target.drive.upload(
                target.folder_id,
                path,
                download_name(path.name),
                mime,
            )
        except (DriveError, SignInError) as exc:
            logger.warning("drive upload %s failed: %s", upload.id, exc, exc_info=True)
            store.finish(upload.id, error=str(exc))
            return
        store.finish(upload.id, drive_file_id=made.id, drive_url=made.url)
        logger.info(
            "drive upload %s: %s -> %s",
            upload.id,
            path.name,
            upload.folder_name,
        )
