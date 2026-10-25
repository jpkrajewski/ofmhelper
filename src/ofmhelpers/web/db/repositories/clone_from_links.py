"""/clone-links results on their way to Drive (`clone_from_links`). Uncached:
the page polls it while reviews run."""

from __future__ import annotations

import time
from typing import Any

from sqlalchemy import and_, or_, select, update
from sqlalchemy.dialects.postgresql import insert

from ofmhelpers.web.db.models import CloneFromLinkRow
from ofmhelpers.web.db.repositories.cached_repository import CachedRepository
from ofmhelpers.web.db.session import session_scope

_COLUMNS = [c.name for c in CloneFromLinkRow.__table__.columns]
_RUNNING = ("checking", "uploading")


def _to_dict(row: CloneFromLinkRow) -> dict[str, Any]:
    return {name: getattr(row, name) for name in _COLUMNS}


class CloneFromLinkRepository(CachedRepository):
    cache_namespace = "clone_from_link"

    def start(
        self, job_id: str, file_index: int, user_id: int, stale_before: float
    ) -> int | None:
        """The review's id, (re)started as `checking`; None when this file
        is reviewed already or under review. A failed one starts again, and
        so does one running since before `stale_before` (cut off)."""
        row = CloneFromLinkRow
        now = time.time()
        fresh = {
            "status": "checking",
            "check_id": None,
            "folder_name": None,
            "drive_file_id": None,
            "drive_url": None,
            "error": None,
            "created_at": now,
            "finished_at": None,
        }
        with session_scope() as s:
            found = s.execute(
                insert(row)
                .values(job_id=job_id, file_index=file_index, user_id=user_id, **fresh)
                .on_conflict_do_update(
                    constraint="uq_clone_from_links_file",
                    set_=fresh,
                    where=or_(
                        row.status == "failed",
                        and_(row.status.in_(_RUNNING), row.created_at < stale_before),
                    ),
                )
                .returning(row.id)
            )
            return found.scalar_one_or_none()

    def get(self, review_id: int) -> dict[str, Any] | None:
        with session_scope() as s:
            row = s.get(CloneFromLinkRow, review_id)
            return _to_dict(row) if row else None

    def for_jobs(self, job_ids: list[str]) -> list[dict[str, Any]]:
        """Every review of these jobs' results, by job then file."""
        if not job_ids:
            return []
        with session_scope() as s:
            rows = s.execute(
                select(CloneFromLinkRow)
                .where(CloneFromLinkRow.job_id.in_(job_ids))
                .order_by(CloneFromLinkRow.job_id, CloneFromLinkRow.file_index)
            ).scalars()
            return [_to_dict(r) for r in rows]

    def checked(self, review_id: int, check_id: int, folder_name: str) -> None:
        """Checked: now uploading into `folder_name`."""
        with session_scope() as s:
            s.execute(
                update(CloneFromLinkRow)
                .where(CloneFromLinkRow.id == review_id)
                .values(status="uploading", check_id=check_id, folder_name=folder_name)
            )

    def finish(
        self,
        review_id: int,
        *,
        drive_file_id: str | None = None,
        drive_url: str | None = None,
        error: str | None = None,
    ) -> None:
        with session_scope() as s:
            s.execute(
                update(CloneFromLinkRow)
                .where(CloneFromLinkRow.id == review_id)
                .values(
                    status="failed" if error else "done",
                    drive_file_id=drive_file_id,
                    drive_url=drive_url,
                    error=error,
                    finished_at=time.time(),
                )
            )
