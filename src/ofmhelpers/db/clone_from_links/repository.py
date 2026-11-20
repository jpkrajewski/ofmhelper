import time

from sqlalchemy import (
    ColumnElement,
    Update,
    and_,
    case,
    exists,
    func,
    or_,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import insert

from ofmhelpers.db.cached_repository import CachedRepository
from ofmhelpers.db.clone_ai_checks.schema import CloneFromLink
from ofmhelpers.db.jobs.schema import Job
from ofmhelpers.db.models import CloneFromLinkRow, JobRow
from ofmhelpers.db.session import session_scope

_COLUMNS = [c.name for c in CloneFromLinkRow.__table__.columns]
_RUNNING = ("checking", "uploading")


def _review(row: CloneFromLinkRow) -> CloneFromLink:
    return CloneFromLink.model_validate({name: getattr(row, name) for name in _COLUMNS})


def _held(claimed: float | None) -> ColumnElement[bool]:
    started = CloneFromLinkRow.started_at
    return started.is_(None) if claimed is None else started == claimed


def _changed(statement: Update) -> bool:
    with session_scope() as s:
        found = s.execute(statement.returning(CloneFromLinkRow.id))
        return found.scalar_one_or_none() is not None


class CloneFromLinkRepository(CachedRepository):
    cache_namespace = "clone_from_link"

    def start(
        self,
        job_id: str,
        file_index: int,
        user_id: int,
        path: str | None,
        stale_before: float,
    ) -> int | None:
        row = CloneFromLinkRow
        fresh = {
            "status": "checking",
            "check_id": None,
            "folder_name": None,
            "drive_file_id": None,
            "drive_url": None,
            "error": None,
            "path": path,
            "attempts": 0,
            "created_at": time.time(),
            "started_at": None,
            "finished_at": None,
        }
        since = func.coalesce(row.started_at, row.created_at)
        with session_scope() as s:
            found = s.execute(
                insert(row)
                .values(job_id=job_id, file_index=file_index, user_id=user_id, **fresh)
                .on_conflict_do_update(
                    constraint="uq_clone_from_links_file",
                    set_=fresh,
                    where=and_(
                        row.user_id == user_id,
                        or_(
                            row.status == "failed",
                            and_(row.status.in_(_RUNNING), since < stale_before),
                        ),
                    ),
                )
                .returning(row.id)
            )
            return found.scalar_one_or_none()

    def get(self, review_id: int) -> CloneFromLink | None:
        with session_scope() as s:
            row = s.get(CloneFromLinkRow, review_id)
            return _review(row) if row else None

    def claim(self, review_id: int) -> CloneFromLink | None:
        row = CloneFromLinkRow
        with session_scope() as s:
            found = s.execute(
                update(row)
                .where(
                    row.id == review_id,
                    row.status.in_(_RUNNING),
                    row.started_at.is_(None),
                )
                .values(started_at=time.time(), attempts=row.attempts + 1)
                .returning(*CloneFromLinkRow.__table__.columns)
            )
            claimed = found.mappings().one_or_none()
            return CloneFromLink.model_validate(dict(claimed)) if claimed else None

    def for_jobs(
        self, job_ids: list[str], user_id: int | None = None
    ) -> list[CloneFromLink]:
        if not job_ids:
            return []
        query = select(CloneFromLinkRow).where(CloneFromLinkRow.job_id.in_(job_ids))
        if user_id is not None:
            query = query.where(CloneFromLinkRow.user_id == user_id)
        with session_scope() as s:
            rows = s.execute(
                query.order_by(CloneFromLinkRow.job_id, CloneFromLinkRow.file_index)
            ).scalars()
            return [_review(r) for r in rows]

    def checked(
        self,
        review_id: int,
        claimed: float | None,
        check_id: int | None,
        folder_name: str,
    ) -> bool:
        row = CloneFromLinkRow
        return _changed(
            update(row)
            .where(row.id == review_id, row.status == "checking", _held(claimed))
            .values(status="uploading", check_id=check_id, folder_name=folder_name)
        )

    def finish(
        self,
        review_id: int,
        *,
        claimed: float | None,
        drive_file_id: str | None = None,
        drive_url: str | None = None,
        error: str | None = None,
    ) -> bool:
        row = CloneFromLinkRow
        return _changed(
            update(row)
            .where(row.id == review_id, row.status.in_(_RUNNING), _held(claimed))
            .values(
                status="failed" if error else "done",
                drive_file_id=drive_file_id,
                drive_url=drive_url,
                error=error,
                finished_at=time.time(),
            )
        )

    def cut_off(self, stale_before: float) -> list[CloneFromLink]:
        row = CloneFromLinkRow
        with session_scope() as s:
            rows = s.execute(
                select(row)
                .where(row.status.in_(_RUNNING), row.started_at < stale_before)
                .order_by(row.id)
            ).scalars()
            return [_review(r) for r in rows]

    def never_claimed(self, created_before: float) -> list[CloneFromLink]:
        row = CloneFromLinkRow
        with session_scope() as s:
            rows = s.execute(
                select(row)
                .where(
                    row.status.in_(_RUNNING),
                    row.started_at.is_(None),
                    row.created_at < created_before,
                )
                .order_by(row.id)
            ).scalars()
            return [_review(r) for r in rows]

    def release(self, review_id: int, claimed: float | None) -> bool:
        row = CloneFromLinkRow
        return _changed(
            update(row)
            .where(row.id == review_id, row.status.in_(_RUNNING), _held(claimed))
            .values(started_at=None)
        )

    def failed_since(self, since: float) -> list[CloneFromLink]:
        row = CloneFromLinkRow
        with session_scope() as s:
            rows = s.execute(
                select(row)
                .where(
                    row.status == "failed",
                    row.drive_file_id.is_(None),
                    row.finished_at > since,
                )
                .order_by(row.id)
            ).scalars()
            return [_review(r) for r in rows]

    def reopen(self, review_id: int, finished_at: float | None) -> bool:
        row = CloneFromLinkRow
        return _changed(
            update(row)
            .where(
                row.id == review_id,
                row.status == "failed",
                row.finished_at == finished_at,
            )
            .values(
                status=case(
                    (row.folder_name.is_not(None), "uploading"), else_="checking"
                ),
                error=None,
                finished_at=None,
                started_at=None,
            )
        )

    def unreviewed_jobs(self, task: str, since: float) -> list[Job]:
        job = JobRow
        reviewed = exists().where(CloneFromLinkRow.job_id == job.id)
        with session_scope() as s:
            rows = s.execute(
                select(
                    job.id,
                    job.task,
                    job.user_id,
                    job.status,
                    job.result,
                    job.created_at,
                )
                .where(
                    job.task == task,
                    job.status == "done",
                    job.finished_at > since,
                    ~reviewed,
                )
                .order_by(job.created_at)
            ).mappings()
            return [Job.model_validate(dict(r)) for r in rows]
