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
from ofmhelpers.features.storage.constants import storage_constants
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


def _creators(owner: int | None) -> _Creators:
    return _Creators(
        by_handle={
            a.handle: a.creator
            for a in list_accounts(owner)
            if a.platform == storage_constants().upload_platform and a.creator
        }
    )


def _handle(username: str) -> str:
    try:
        return get_platform(storage_constants().upload_platform).handle(username)
    except KeyError, ValueError:  # silent: not a handle: kept as typed
        return username


def _files(job_id: str, readers: Readers) -> list[_UploadFile]:
    job = get_job(job_id, readers)
    if job is None or job.status != "done":
        return []
    return [
        _UploadFile(index=index, path=Path(raw))
        for index, raw in enumerate(job.local_paths())
        if raw is not None and Path(raw).is_file()
    ]


def _enqueue(upload_ids: list[int], actor: str) -> None:
    job_id = create_job(
        storage_constants().upload_task, {"upload_ids": upload_ids}, actor=actor
    )
    try:
        enqueue(run_job, (job_id, upload_files, {"upload_ids": upload_ids}))
    except Exception as exc:
        logger.warning("drive uploads %s not queued", upload_ids, exc_info=True)
        fail_job(job_id, "Could not queue the upload")
        for upload_id in upload_ids:
            store.finish(upload_id, error=f"Could not queue the upload: {exc}")


class StorageServiceUploads(BaseModel, frozen=True, arbitrary_types_allowed=True):
    client: SkipValidation[StorageClient] | None
    user_id: int
    folders: StorageServiceFolders
    review: StorageServiceReview
    va_folders: StorageServiceVaFolders

    @property
    def actor(self) -> str:
        return str(self.user_id)

    def connected(self) -> bool:
        return self.client is not None

    def targets(self) -> list[UploadTarget]:
        folders = drive_folders.account_folders()
        creators = _creators(self.user_id)
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
                    creator=creators.of(_handle(t.handle)),
                    waiting=len(folder.scan.waiting) if folder.scan else None,
                    followers=t.followers,
                )
            )
        return found

    def _va_targets(self, va_ids: list[int]) -> list[VaFolder]:
        theirs = {f.user_id: f for f in self.va_folders.of_owner()}
        if not set(va_ids) <= set(theirs):
            msg = "That VA has no Drive folder you can upload into: Sync on /drive"
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
            msg = "Pick a folder"
            raise RefusedError(msg)
        mine = {t.login_id for t in drive_folders.targets(self.user_id)}
        if (va_ids or mine & set(login_ids)) and self.client is None:
            msg = storage_constants().connect_first
            raise RefusedError(msg)
        usable = {t.login_id: t for t in self.targets()}
        if not set(login_ids) <= mine or not set(login_ids) <= set(usable):
            msg = "That account has no Drive folder you can upload into: Sync on /drive"
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
                    job_id, index, login_id, self.user_id, usable[login_id].folder_name
                )
            )
            is not None
        ] + [
            upload_id
            for folder in vas
            for job_id, index in files
            if (
                upload_id := store.queue(
                    job_id, index, None, self.user_id, folder.name, va_id=folder.user_id
                )
            )
            is not None
        ]
        if ids:
            _enqueue(ids, self.actor)
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
            queued=sum(1 for u in uploads if u.status == "queued"), uploads=uploads
        )

    def remove(self, upload_id: int, readers: Readers = None) -> DriveUpload:
        found = store.get(upload_id)
        readers = readers if readers is not None else self.actor
        if (
            found is None
            or store.uploader(upload_id) != self.user_id
            or get_job(found.job_id, readers) is None
        ):
            msg = "No such upload"
            raise RefusedError(msg)
        if found.status != "done":
            msg = "Only an uploaded video can be removed"
            raise RefusedError(msg)
        file_id = store.file_id(upload_id)
        if not file_id or self.client is None:
            msg = storage_constants().connect_first
            raise RefusedError(msg)
        try:
            self.client.trash_file(file_id)
        except FolderGoneError:  # silent: already gone in Drive
            logger.info("drive upload %s was already gone in Drive", upload_id)
        except (SignInError, DriveNotSetUpError) as exc:
            logger.warning(
                "drive upload %s: sign-in refused: %s", upload_id, exc, exc_info=True
            )
            msg = storage_constants().connect_first
            raise RefusedError(msg) from exc
        except DriveError as exc:
            logger.warning("drive upload %s: not removed", upload_id, exc_info=True)
            msg = "Google Drive did not remove it. Try again."
            raise UnavailableError(msg) from exc
        store.remove(upload_id)
        logger.info("drive upload %s removed from %s", upload_id, found.folder_name)
        return found.model_copy(update={"status": "removed"})

    def _destination(
        self, upload: DriveUpload, folder: Folder, drive: StorageClient
    ) -> ReviewPlace:
        job = get_job(upload.job_id, None)
        if (
            job is None
            or job.task != storage_constants().spoofed_task
            or isinstance(folder, VaFolder)
        ):
            return ReviewPlace(drive=drive, folder_id=folder.folder_id)
        place = self.review.target()
        spoofed = self.review.account_subfolder(
            place, folder.folder_id, AccountFolder.SPOOFED
        )
        return ReviewPlace(drive=place.drive, folder_id=spoofed)

    def upload(self, upload: DriveUpload, folder: Folder) -> None:
        files = {f.index: f.path for f in _files(upload.job_id, None)}
        path = files.get(upload.file_index)
        if path is None:
            store.finish(upload.id, error="The file is no longer on the server")
            return
        drive = self.client
        if drive is None:
            store.finish(
                upload.id, error="The folder owner's Google Drive was disconnected"
            )
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
                    error="The account's folder is not in its owner's"
                    f" {self.folders.root_name()}"
                    " folder: Sync on /drive, then upload again",
                )
                return
            target = self._destination(upload, folder, drive)
            made = target.drive.upload(
                target.folder_id, path, download_name(path.name), mime
            )
        except (DriveError, SignInError) as exc:
            logger.warning("drive upload %s failed: %s", upload.id, exc, exc_info=True)
            store.finish(upload.id, error=str(exc))
            return
        store.finish(upload.id, drive_file_id=made.id, drive_url=made.url)
        logger.info(
            "drive upload %s: %s -> %s", upload.id, path.name, upload.folder_name
        )
