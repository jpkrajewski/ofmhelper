"""Clone results uploaded into account Drive folders (`drive_uploads`).
Uncached: the clone page polls it while uploads run."""

from __future__ import annotations

import time

from sqlalchemy import and_, or_, select, update
from sqlalchemy.dialects.postgresql import insert

from ofmhelpers.web.db.models import DriveUploadRow
from ofmhelpers.web.db.repositories.cached_repository import CachedRepository
from ofmhelpers.web.db.session import session_scope


def _to_dict(row: DriveUploadRow) -> dict:
    return {
        "id": row.id,
        "job_id": row.job_id,
        "file_index": row.file_index,
        "login_id": row.login_id,
        "user_id": row.user_id,
        "folder_name": row.folder_name,
        "status": row.status,
        "drive_url": row.drive_url,
        "error": row.error,
    }


class DriveUploadRepository(CachedRepository):
    cache_namespace = "drive_upload"

    def queue(
        self,
        job_id: str,
        file_index: int,
        login_id: int,
        user_id: int,
        folder_name: str,
        stale_before: float,
    ) -> int | None:
        """The upload's id, queued; None when this file is already in that
        folder or on its way. A failed one is queued again, and so is one
        still queued since before `stale_before` (stranded)."""
        row = DriveUploadRow
        with session_scope() as s:
            found = s.execute(
                insert(row)
                .values(
                    job_id=job_id,
                    file_index=file_index,
                    login_id=login_id,
                    user_id=user_id,
                    folder_name=folder_name,
                    status="queued",
                    created_at=time.time(),
                )
                .on_conflict_do_update(
                    constraint="uq_drive_uploads_file_folder",
                    set_={
                        "status": "queued",
                        "user_id": user_id,
                        "error": None,
                        "created_at": time.time(),
                        "finished_at": None,
                    },
                    where=or_(
                        row.status == "failed",
                        and_(row.status == "queued", row.created_at < stale_before),
                    ),
                )
                .returning(row.id)
            )
            return found.scalar_one_or_none()

    def get(self, upload_id: int) -> dict | None:
        with session_scope() as s:
            row = s.get(DriveUploadRow, upload_id)
            return _to_dict(row) if row else None

    def for_jobs(self, job_ids: list[str]) -> list[dict]:
        """Every upload of these jobs, oldest first."""
        if not job_ids:
            return []
        with session_scope() as s:
            rows = s.execute(
                select(DriveUploadRow)
                .where(DriveUploadRow.job_id.in_(job_ids))
                .order_by(DriveUploadRow.id)
            ).scalars()
            return [_to_dict(r) for r in rows]

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
                )
            )
