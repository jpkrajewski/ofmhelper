"""
ofmhelpers/web/stores/drive_uploads.py

Clone results uploaded into account Drive folders (features/drive/uploads.py
queues and runs them; /clone shows where each result went).
"""

import time
from functools import lru_cache

from ofmhelpers.config import settings
from ofmhelpers.db.repositories import DriveUploadRepository
lazy from ofmhelpers.schemas import DriveUpload


@lru_cache(maxsize=1)
def _repository() -> DriveUploadRepository:
    return DriveUploadRepository()


def queue(
    job_id: str, file_index: int, login_id: int, user_id: int, folder_name: str
) -> int | None:
    """The new upload's id; None when the file is already there or on its
    way (a failed one is queued again, and one queued longer than
    `settings.gdrive.upload_stale_s` ago: stranded)."""
    stale_before = time.time() - settings.gdrive.upload_stale_s
    return _repository().queue(
        job_id, file_index, login_id, user_id, folder_name, stale_before
    )


def get(upload_id: int) -> DriveUpload | None:
    return _repository().get(upload_id)


def uploader(upload_id: int) -> int | None:
    """Whose Google account it goes up as."""
    return _repository().uploader(upload_id)


def for_jobs(job_ids: list[str]) -> list[DriveUpload]:
    return _repository().for_jobs(job_ids)


def file_id(upload_id: int) -> str | None:
    """The uploaded file's Drive id (done uploads only)."""
    return _repository().file_id(upload_id)


def remove(upload_id: int, reason: str = "Removed from Drive") -> None:
    _repository().remove(upload_id, reason)


def gone_from(login_id: int, present: set[str], before: float) -> int:
    """This login's uploads whose file left its account folder and POSTED
    (deleted or moved away in Drive) before `before`: marked removed."""
    return _repository().gone_from(login_id, present, before)


def finish(
    upload_id: int,
    *,
    drive_file_id: str | None = None,
    drive_url: str | None = None,
    error: str | None = None,
) -> None:
    """Done (with the Drive file), or failed with `error`."""
    _repository().finish(
        upload_id, drive_file_id=drive_file_id, drive_url=drive_url, error=error
    )
