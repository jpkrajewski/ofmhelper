"""
ofmhelpers/web/drive_uploads.py

/clone results into an Instagram account's Drive folder ("<handle> -
<owner>", web/drive_folders.py), so they are on the phone's Drive ready to
post.

The folder lives in its phone owner's own Drive, so files go up through
the owner's Google account (`stores.users.refresh_token`), like everything
/drive does. Only the owner uploads into their folders (admins too:
nobody uses another person's).

`queue_uploads` checks everything up front (the folder is one the person
may use, the jobs are theirs and finished, the folder's owner connected
Google Drive) and queues one worker job; `upload_files` runs there. A file already in that folder,
or on its way, is never uploaded again; a failed one may be retried, and so
may one stranded `queued` (`settings.gdrive.upload_stale_s`: the worker was
killed mid-batch). A job that cannot be queued, and any error uploading one
file, marks the upload failed with a reason: nothing stays `queued` silently.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path

from ofmhelpers.cache import enqueue
from ofmhelpers.gdrive import DriveError, connect_as
from ofmhelpers.log import get_logger
from ofmhelpers.signin import SignInError
from ofmhelpers.web.download_names import download_name
from ofmhelpers.web.drive_folders import root_name, usable_root
from ofmhelpers.web.schemas import DriveUpload, SessionUser, StoredFolder, UploadTarget
from ofmhelpers.web.stores import drive_folders
from ofmhelpers.web.stores import drive_uploads as store
from ofmhelpers.web.stores.jobs import get_job
from ofmhelpers.web.stores.users import refresh_token

logger = get_logger(__name__)


class UploadRefusedError(ValueError):
    """Nothing was queued; the message says why (shown on the page)."""


def targets(owner: int | None) -> list[UploadTarget]:
    """The account folders `owner` may upload into:
    their phones' Instagram logins that have a folder."""
    folders = drive_folders.account_folders()
    return [
        UploadTarget(login_id=t.login_id, folder_name=t.folder_name)
        for t in drive_folders.targets(owner)
        if t.login_id in folders
    ]


def _folder_owner(login_id: int) -> int | None:
    found = next((t for t in drive_folders.targets() if t.login_id == login_id), None)
    return found.device.user_id if found else None


def _files(job_id: str, actor: str | None) -> list[tuple[int, Path]]:
    """(index, path) of every result video still on disk of a finished job
    the session may see."""
    job = get_job(job_id, actor)
    if job is None or job.status != "done":
        return []
    return [
        (index, Path(raw))
        for index, raw in enumerate(job.local_paths())
        if raw is not None and Path(raw).is_file()
    ]


def queue_uploads(
    user: SessionUser, job_ids: list[str], login_id: int
) -> list[DriveUpload]:
    """Queues every result of `job_ids` into `login_id`'s folder. Raises
    `UploadRefusedError`."""
    mine = next(
        (t for t in drive_folders.targets(user.id) if t.login_id == login_id), None
    )
    if mine is not None and not refresh_token(user.id):
        msg = "Connect Google Drive on your Account page first"
        raise UploadRefusedError(msg)
    target = next((t for t in targets(user.id) if t.login_id == login_id), None)
    if mine is None or target is None:
        msg = "That account has no Drive folder you can upload into: Sync on /drive"
        raise UploadRefusedError(msg)
    actor = user.actor
    ids = [
        upload_id
        for job_id in job_ids
        for index, _path in _files(job_id, actor)
        if (
            upload_id := store.queue(
                job_id, index, login_id, user.id, target.folder_name
            )
        )
        is not None
    ]
    if ids:
        _enqueue(ids)
    return store.for_jobs([j for j in job_ids if get_job(j, actor) is not None])


def _enqueue(upload_ids: list[int]) -> None:
    """The worker job; when it cannot be queued (Redis down), the uploads
    fail with that reason, so Upload may retry them."""
    try:
        enqueue(upload_files, upload_ids)
    except Exception as exc:
        logger.warning("drive uploads %s not queued", upload_ids, exc_info=True)
        for upload_id in upload_ids:
            store.finish(upload_id, error=f"Could not queue the upload: {exc}")


def _upload_one(upload: DriveUpload, owner_id: int, folder: StoredFolder) -> None:
    """Into `folder` only while it sits in the owner's usable root (a dev
    copy of the prod database holds the live root's id: refused)."""
    files = dict(_files(upload.job_id, None))
    path = files.get(upload.file_index)
    if path is None:
        store.finish(upload.id, error="The file is no longer on the server")
        return
    token = refresh_token(owner_id)
    if not token:
        store.finish(
            upload.id, error="The folder owner's Google Drive was disconnected"
        )
        return
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    try:
        drive = connect_as(token)
        root = usable_root(drive, owner_id)
        if root is None or folder.root_folder_id != root:
            logger.warning(
                "drive upload %s refused: folder %s not in %s's root %s",
                upload.id,
                folder.folder_id,
                owner_id,
                root,
            )
            store.finish(
                upload.id,
                error=f"The account's folder is not in its owner's {root_name()}"
                " folder: Sync on /drive, then upload again",
            )
            return
        made = drive.upload(folder.folder_id, path, download_name(path.name), mime)
    except (DriveError, SignInError) as exc:
        logger.warning("drive upload %s failed: %s", upload.id, exc)
        store.finish(upload.id, error=str(exc))
        return
    store.finish(upload.id, drive_file_id=made.id, drive_url=made.url)
    logger.info("drive upload %s: %s -> %s", upload.id, path.name, upload.folder_name)


def upload_files(upload_ids: list[int]) -> None:
    """Worker job: each queued upload, one after another (a phone's Drive
    gets them in the order clicked). One failing never stops the rest."""
    folders = drive_folders.account_folders()
    for upload_id in upload_ids:
        found = store.get(upload_id)
        if found is None or found.status != "queued":
            continue
        upload, owner_id = found, _folder_owner(found.login_id)
        folder = folders.get(upload.login_id)
        if folder is None or owner_id is None:
            store.finish(
                upload_id, error="The account's Drive folder is gone: Sync on /drive"
            )
            continue
        try:
            _upload_one(upload, owner_id, folder)
        except Exception as exc:
            # Never left `queued`: a file deleted since the check, a bug.
            logger.warning("drive upload %s failed", upload_id, exc_info=True)
            store.finish(upload_id, error=f"Upload failed: {exc}")
