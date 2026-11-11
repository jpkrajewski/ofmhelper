"""Downloaded files (`downloads`): one row per file of a download job.
Uncached: the sweep, a paste and a job's finish all write, and every read
decides something (fetch again or not, remove or keep)."""

from datetime import datetime

from sqlalchemy import and_, delete, exists, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import aliased

from ofmhelpers.db.models import DownloadRow, JobRow
from ofmhelpers.db.repositories.cached_repository import CachedRepository
from ofmhelpers.db.session import session_scope
from ofmhelpers.schemas.downloads import Download, DownloadedFile, RemovedFile


def _download(row: DownloadRow) -> Download:
    return Download.model_validate(row)


class DownloadRepository(CachedRepository):
    cache_namespace = "download"

    def record(
        self, user_id: int, job_id: str, files: list[DownloadedFile], now: datetime
    ) -> None:
        """The files a job fetched just now, each at its position: a new row,
        or the row a replay finds there updated in place (stored again, its
        paths, `last_used_at`; `created_at` kept). One transaction."""
        row = DownloadRow
        with session_scope() as s:
            for file in files:
                fresh = {
                    "url": file.url,
                    "file_path": file.file_path,
                    "asset_path": file.asset_path,
                    "stored": True,
                    "removed_at": None,
                    "last_used_at": now,
                }
                s.execute(
                    insert(row)
                    .values(
                        user_id=user_id,
                        job_id=job_id,
                        position=file.position,
                        created_at=now,
                        **fresh,
                    )
                    .on_conflict_do_update(
                        constraint="uq_downloads_job_position", set_=fresh
                    )
                )

    def drop_from(self, job_id: str, position: int) -> None:
        """The job's rows at `position` and after: a replay that got fewer
        files than the run before."""
        with session_scope() as s:
            s.execute(
                delete(DownloadRow).where(
                    DownloadRow.job_id == job_id, DownloadRow.position >= position
                )
            )

    def of_job(self, job_id: str) -> list[Download]:
        with session_scope() as s:
            rows = s.execute(
                select(DownloadRow)
                .where(DownloadRow.job_id == job_id)
                .order_by(DownloadRow.position)
            ).scalars()
            return [_download(r) for r in rows]

    def get(self, job_id: str, position: int) -> Download | None:
        with session_scope() as s:
            row = s.execute(
                select(DownloadRow).where(
                    DownloadRow.job_id == job_id, DownloadRow.position == position
                )
            ).scalar_one_or_none()
            return _download(row) if row else None

    def newest_of(self, user_id: int, urls: list[str]) -> list[Download]:
        """For each of `urls` `user_id` downloaded: the files of the newest
        job that fetched it, by url then position. Their own only."""
        if not urls:
            return []
        row, later = DownloadRow, aliased(DownloadRow)
        newer = exists().where(
            later.user_id == user_id,
            later.url == row.url,
            later.job_id != row.job_id,
            (later.created_at > row.created_at)
            | and_(later.created_at == row.created_at, later.id > row.id),
        )
        with session_scope() as s:
            rows = s.execute(
                select(row)
                .where(row.user_id == user_id, row.url.in_(urls), ~newer)
                .order_by(row.url, row.position)
            ).scalars()
            return [_download(r) for r in rows]

    def stored_anywhere(self, urls: list[str]) -> list[Download]:
        """For each of `urls` anyone has stored: the files of the newest job
        that fetched it and still has them all, by url then position. A
        link is the same video whoever pasted it: nobody fetches twice what
        the server already holds (owner 2026-11-12: "why not everyone?")."""
        if not urls:
            return []
        row, later, gone = DownloadRow, aliased(DownloadRow), aliased(DownloadRow)
        # A job counts only while every file it fetched for the link is stored.
        whole = ~exists().where(
            gone.job_id == row.job_id, gone.url == row.url, ~gone.stored
        )
        later_gone = aliased(DownloadRow)
        newer = exists().where(
            later.url == row.url,
            later.job_id != row.job_id,
            later.stored,
            ~exists().where(
                later_gone.job_id == later.job_id,
                later_gone.url == later.url,
                ~later_gone.stored,
            ),
            (later.created_at > row.created_at)
            | and_(later.created_at == row.created_at, later.id > row.id),
        )
        with session_scope() as s:
            rows = s.execute(
                select(row)
                .where(row.url.in_(urls), whole, ~newer)
                .order_by(row.url, row.position)
            ).scalars()
            return [_download(r) for r in rows]

    def file_shared(self, download_id: int, file_path: str) -> bool:
        """Whether another row still stored is the same file on disk
        (someone else's card of a link reused, not fetched again)."""
        with session_scope() as s:
            return s.execute(
                select(
                    exists().where(
                        DownloadRow.id != download_id,
                        DownloadRow.stored,
                        DownloadRow.file_path == file_path,
                    )
                )
            ).scalar_one()

    def touch(self, job_id: str, url: str, now: datetime) -> None:
        """Asked for again: `last_used_at` of the job's files of `url`."""
        with session_scope() as s:
            s.execute(
                update(DownloadRow)
                .where(DownloadRow.job_id == job_id, DownloadRow.url == url)
                .values(last_used_at=now)
            )

    def removed_in(self, job_ids: list[str]) -> list[RemovedFile]:
        """The files of these jobs removed on purpose (`stored` false)."""
        if not job_ids:
            return []
        with session_scope() as s:
            rows = s.execute(
                select(DownloadRow.job_id, DownloadRow.position).where(
                    DownloadRow.job_id.in_(job_ids), DownloadRow.stored.is_(False)
                )
            )
            return [RemovedFile(job_id=j, position=p) for j, p in rows]

    def by_asset(self, user_id: int, fragment: str) -> list[Download]:
        """`user_id`'s removed files whose asset-store copy holds `fragment`
        in its path (a content digest), the newest first."""
        with session_scope() as s:
            rows = s.execute(
                select(DownloadRow)
                .where(
                    DownloadRow.user_id == user_id,
                    DownloadRow.stored.is_(False),
                    DownloadRow.asset_path.contains(fragment, autoescape=True),
                )
                .order_by(DownloadRow.created_at.desc(), DownloadRow.id.desc())
            ).scalars()
            return [_download(r) for r in rows]

    # --- the retention sweep (features/downloads/cleanup.py) ---------------------

    def idle(self, before: datetime, after_id: int, limit: int) -> list[Download]:
        """Stored files nobody asked for and no job used since `before`, by
        id from `after_id` on, at most `limit`: what a sweep round looks at.
        Never one whose own job is running (it is being fetched again)."""
        running = exists().where(
            JobRow.id == DownloadRow.job_id, JobRow.status == "running"
        )
        with session_scope() as s:
            rows = s.execute(
                select(DownloadRow)
                .where(
                    DownloadRow.stored,
                    DownloadRow.last_used_at < before,
                    DownloadRow.id > after_id,
                    ~running,
                )
                .order_by(DownloadRow.id)
                .limit(limit)
            ).scalars()
            return [_download(r) for r in rows]

    def set_asset_path(self, download_id: int, asset_path: str) -> None:
        with session_scope() as s:
            s.execute(
                update(DownloadRow)
                .where(DownloadRow.id == download_id)
                .values(asset_path=asset_path)
            )

    def note_use(self, download_id: int, used_at: datetime) -> None:
        """A job used it at `used_at`: `last_used_at` moves there if later."""
        with session_scope() as s:
            s.execute(
                update(DownloadRow)
                .where(DownloadRow.id == download_id)
                .values(last_used_at=func.greatest(DownloadRow.last_used_at, used_at))
            )

    def mark_removed(
        self, download_id: int, idle_since: datetime, now: datetime
    ) -> bool:
        """Not stored any more, unless it was asked for or used after
        `idle_since` (what the sweep saw) or is gone already: False then,
        and nothing is to be deleted."""
        with session_scope() as s:
            found = s.execute(
                update(DownloadRow)
                .where(
                    DownloadRow.id == download_id,
                    DownloadRow.stored,
                    DownloadRow.last_used_at <= idle_since,
                )
                .values(stored=False, removed_at=now)
                .returning(DownloadRow.id)
            )
            return found.scalar_one_or_none() is not None

    def put_back(self, download_id: int) -> None:
        """Stored after all: its file could not be deleted."""
        with session_scope() as s:
            s.execute(
                update(DownloadRow)
                .where(DownloadRow.id == download_id)
                .values(stored=True, removed_at=None)
            )

    def asset_shared(self, download_id: int, asset_path: str) -> bool:
        """Whether another file still stored has the same asset-store copy
        (the same content downloaded twice)."""
        with session_scope() as s:
            return s.execute(
                select(
                    exists().where(
                        DownloadRow.id != download_id,
                        DownloadRow.stored,
                        DownloadRow.asset_path == asset_path,
                    )
                )
            ).scalar_one()
