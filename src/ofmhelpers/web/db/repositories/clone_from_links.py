"""/clone-links results on their way to Drive (`clone_from_links`). Uncached:
the page polls it while reviews run.

A worker owns a row from its `claim` (`started_at` set, `attempts` + 1)
until it finishes it; `checked` and `finish` change only the row as that
claim left it (`started_at` equal), so a run whose row was restarted or
taken over meanwhile writes nothing."""

from __future__ import annotations

import time
from typing import Any

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

from ofmhelpers.web.db.models import CloneFromLinkRow, JobRow
from ofmhelpers.web.db.repositories.cached_repository import CachedRepository
from ofmhelpers.web.db.session import session_scope

_COLUMNS = [c.name for c in CloneFromLinkRow.__table__.columns]
_RUNNING = ("checking", "uploading")


def _to_dict(row: CloneFromLinkRow) -> dict[str, Any]:
    return {name: getattr(row, name) for name in _COLUMNS}


def _held(claimed: float | None) -> ColumnElement[bool]:
    """The row as a claim left it: claimed at `claimed`, or never claimed
    (None: what `start_reviews` finishes when it cannot queue it)."""
    started = CloneFromLinkRow.started_at
    return started.is_(None) if claimed is None else started == claimed


def _changed(statement: Update) -> bool:
    """Whether the UPDATE changed its one row."""
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
        """The review's id, (re)started as `checking` of the file at `path`;
        None when this file is reviewed already, under review, or another
        user's row. A failed one starts again, and so does one running since
        before `stale_before` (claimed then, or queued then and never
        claimed)."""
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

    def get(self, review_id: int) -> dict[str, Any] | None:
        with session_scope() as s:
            row = s.get(CloneFromLinkRow, review_id)
            return _to_dict(row) if row else None

    def claim(self, review_id: int) -> dict[str, Any] | None:
        """The row, claimed for this worker (`started_at` now, one more
        attempt); None when it is not running or another worker holds it
        (a second queued job for the same review)."""
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
            return dict(claimed) if claimed else None

    def for_jobs(
        self, job_ids: list[str], user_id: int | None = None
    ) -> list[dict[str, Any]]:
        """Every review of these jobs' results (only `user_id`'s when
        given), by job then file."""
        if not job_ids:
            return []
        query = select(CloneFromLinkRow).where(CloneFromLinkRow.job_id.in_(job_ids))
        if user_id is not None:
            query = query.where(CloneFromLinkRow.user_id == user_id)
        with session_scope() as s:
            rows = s.execute(
                query.order_by(CloneFromLinkRow.job_id, CloneFromLinkRow.file_index)
            ).scalars()
            return [_to_dict(r) for r in rows]

    def checked(
        self,
        review_id: int,
        claimed: float | None,
        check_id: int | None,
        folder_name: str,
    ) -> bool:
        """Checked: now uploading into `folder_name`. False when the row is
        no longer this claim's (restarted, taken over, deleted with its
        job)."""
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
        """Done (with the Drive file), or failed with `error`; False when the
        row is no longer as `claimed` left it."""
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

    # --- recovery (web/clone_links/review.py resume_reviews) ----------------

    def cut_off(self, stale_before: float) -> list[dict[str, Any]]:
        """Running rows a worker claimed before `stale_before`."""
        row = CloneFromLinkRow
        with session_scope() as s:
            rows = s.execute(
                select(row)
                .where(row.status.in_(_RUNNING), row.started_at < stale_before)
                .order_by(row.id)
            ).scalars()
            return [_to_dict(r) for r in rows]

    def release(self, review_id: int, claimed: float | None) -> bool:
        """The claim made at `claimed` dropped (its worker is gone): the row
        waits for a worker again, check and folder kept."""
        row = CloneFromLinkRow
        return _changed(
            update(row)
            .where(row.id == review_id, row.status.in_(_RUNNING), _held(claimed))
            .values(started_at=None)
        )

    def failed_since(self, since: float) -> list[dict[str, Any]]:
        """Failed rows with nothing in Drive, finished after `since`."""
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
            return [_to_dict(r) for r in rows]

    def reopen(self, review_id: int, finished_at: float | None) -> bool:
        """A failed row (as it finished at `finished_at`) running again:
        `uploading` when its check is saved (`folder_name`), else
        `checking`; unclaimed, error cleared."""
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

    def unreviewed_jobs(self, task: str, since: float) -> list[dict[str, Any]]:
        """`task`'s done jobs finished after `since` with no review row: a
        run whose review never started. Only what `Job` needs to read its
        result; never the healing job listing."""
        job = JobRow
        reviewed = exists().where(CloneFromLinkRow.job_id == job.id)
        with session_scope() as s:
            rows = s.execute(
                select(
                    job.id, job.task, job.actor, job.status, job.result, job.created_at
                )
                .where(
                    job.task == task,
                    job.status == "done",
                    job.finished_at > since,
                    ~reviewed,
                )
                .order_by(job.created_at)
            ).mappings()
            return [dict(r) for r in rows]
