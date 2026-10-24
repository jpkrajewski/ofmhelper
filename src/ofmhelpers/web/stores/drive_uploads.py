"""
ofmhelpers/web/stores/drive_uploads.py

Clone results uploaded into account Drive folders (web/drive_uploads.py
queues and runs them; /clone shows where each result went).
"""

from __future__ import annotations

import time
from functools import lru_cache

from ofmhelpers.config import settings
from ofmhelpers.web.db.repositories import DriveUploadRepository
from ofmhelpers.web.schemas import DriveUpload


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
    found = _repository().get(upload_id)
    return DriveUpload.model_validate(found) if found else None


def uploader(upload_id: int) -> int | None:
    """Whose Google account it goes up as."""
    found = _repository().get(upload_id)
    return found["user_id"] if found else None


def for_jobs(job_ids: list[str]) -> list[DriveUpload]:
    return [DriveUpload.model_validate(r) for r in _repository().for_jobs(job_ids)]


def file_id(upload_id: int) -> str | None:
    """The uploaded file's Drive id (done uploads only)."""
    found = _repository().get(upload_id)
    return found["drive_file_id"] if found else None


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
