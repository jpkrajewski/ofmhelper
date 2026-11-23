# Copyright (C) 2026 jpkrajewski

import time

from sqlalchemy import and_, or_, select, update
from sqlalchemy.dialects.postgresql import insert

from ofmhelpers.db.cached_repository import CachedRepository
from ofmhelpers.db.drive_folders.schema import DriveUpload
from ofmhelpers.db.models import DriveUploadRow
from ofmhelpers.db.session import session_scope

GONE_IN_DRIVE = "Removed in Drive"


def _upload(row: DriveUploadRow) -> DriveUpload:
    return DriveUpload(
        id=row.id,
        job_id=row.job_id,
        file_index=row.file_index,
        login_id=row.login_id,
        va_id=row.va_id,
        folder_name=row.folder_name,
        status=row.status,
        drive_url=row.drive_url,
        error=row.error,
    )


class DriveUploadRepository(CachedRepository):
    cache_namespace = "drive_upload"

    def queue(
        self,
        job_id: str,
        file_index: int,
        login_id: int | None,
        user_id: int,
        folder_name: str,
        stale_before: float,
        va_id: int | None = None,
    ) -> int | None:
        row = DriveUploadRow
        with session_scope() as s:
            found = s.execute(
                insert(row)
                .values(
                    job_id=job_id,
                    file_index=file_index,
                    login_id=login_id,
                    va_id=va_id,
                    user_id=user_id,
                    folder_name=folder_name,
                    status="queued",
                    created_at=time.time(),
                )
                .on_conflict_do_update(
                    constraint=(
                        "uq_drive_uploads_file_va"
                        if va_id is not None
                        else "uq_drive_uploads_file_folder"
                    ),
                    set_={
                        "status": "queued",
                        "user_id": user_id,
                        "error": None,
                        "created_at": time.time(),
                        "finished_at": None,
                    },
                    where=or_(
                        row.status.in_(("failed", "removed")),
                        and_(row.status == "queued", row.created_at < stale_before),
                    ),
                )
                .returning(row.id),
            )
            return found.scalar_one_or_none()

    def get(self, upload_id: int) -> DriveUpload | None:
        with session_scope() as s:
            row = s.get(DriveUploadRow, upload_id)
            return _upload(row) if row else None

    def uploader(self, upload_id: int) -> int | None:
        with session_scope() as s:
            row = s.get(DriveUploadRow, upload_id)
            return row.user_id if row else None

    def file_id(self, upload_id: int) -> str | None:
        with session_scope() as s:
            row = s.get(DriveUploadRow, upload_id)
            return row.drive_file_id if row else None

    def for_jobs(
        self,
        job_ids: list[str],
        uploaders: frozenset[int] | None = None,
    ) -> list[DriveUpload]:
        if not job_ids:
            return []
        query = select(DriveUploadRow).where(DriveUploadRow.job_id.in_(job_ids))
        if uploaders is not None:
            query = query.where(DriveUploadRow.user_id.in_(uploaders))
        with session_scope() as s:
            rows = s.execute(query.order_by(DriveUploadRow.id)).scalars()
            return [_upload(r) for r in rows]

    def made_here(self, file_ids: list[str]) -> list[str]:
        if not file_ids:
            return []
        with session_scope() as s:
            found = s.execute(
                select(DriveUploadRow.drive_file_id).where(
                    DriveUploadRow.drive_file_id.in_(file_ids),
                ),
            ).scalars()
            return [file_id for file_id in found if file_id]

    def remove(self, upload_id: int, reason: str) -> None:
        with session_scope() as s:
            s.execute(
                update(DriveUploadRow)
                .where(DriveUploadRow.id == upload_id)
                .values(status="removed", error=reason),
            )

    def follow_drive(self, login_id: int, present: set[str], before: float) -> int:
        with session_scope() as s:
            rows = s.execute(
                select(DriveUploadRow).where(
                    DriveUploadRow.login_id == login_id,
                    DriveUploadRow.finished_at < before,
                    or_(
                        DriveUploadRow.status == "done",
                        and_(
                            DriveUploadRow.status == "removed",
                            DriveUploadRow.error == GONE_IN_DRIVE,
                        ),
                    ),
                ),
            ).scalars()
            changed = [
                r for r in rows if (r.drive_file_id in present) != (r.status == "done")
            ]
            for row in changed:
                there = row.drive_file_id in present
                row.status = "done" if there else "removed"
                row.error = None if there else GONE_IN_DRIVE
            return len(changed)

    def finish(
        self,
        upload_id: int,
        *,
        drive_file_id: str | None = None,
        drive_url: str | None = None,
        error: str | None = None,
    ) -> None:
        with session_scope() as s:
            s.execute(
                update(DriveUploadRow)
                .where(DriveUploadRow.id == upload_id)
                .values(
                    status="failed" if error else "done",
                    drive_file_id=drive_file_id,
                    drive_url=drive_url,
                    error=error,
                    finished_at=time.time(),
                ),
            )
