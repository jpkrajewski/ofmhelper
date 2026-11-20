import time
from functools import lru_cache

from ofmhelpers.config import settings
from ofmhelpers.db.drive_uploads.repository import DriveUploadRepository
lazy from ofmhelpers.db.drive_folders.schema import DriveUpload


@lru_cache(maxsize=1)
def _repository() -> DriveUploadRepository:
    return DriveUploadRepository()


def queue(
    job_id: str,
    file_index: int,
    login_id: int | None,
    user_id: int,
    folder_name: str,
    va_id: int | None = None,
) -> int | None:
    stale_before = time.time() - settings.gdrive.upload_stale_s
    return _repository().queue(
        job_id, file_index, login_id, user_id, folder_name, stale_before, va_id
    )


def get(upload_id: int) -> DriveUpload | None:
    return _repository().get(upload_id)


def uploader(upload_id: int) -> int | None:
    return _repository().uploader(upload_id)


def for_jobs(
    job_ids: list[str], uploaders: frozenset[int] | None = None
) -> list[DriveUpload]:
    return _repository().for_jobs(job_ids, uploaders)


def file_id(upload_id: int) -> str | None:
    return _repository().file_id(upload_id)


def made_here(file_ids: list[str]) -> list[str]:
    return _repository().made_here(file_ids)


def remove(upload_id: int, reason: str = "Removed from Drive") -> None:
    _repository().remove(upload_id, reason)


def gone_from(login_id: int, present: set[str], before: float) -> int:
    return _repository().gone_from(login_id, present, before)


def finish(
    upload_id: int,
    *,
    drive_file_id: str | None = None,
    drive_url: str | None = None,
    error: str | None = None,
) -> None:
    _repository().finish(
        upload_id, drive_file_id=drive_file_id, drive_url=drive_url, error=error
    )
