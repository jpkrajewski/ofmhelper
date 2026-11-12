"""Downloaded files (`downloads`, one row per file of a download job): what
a finished download writes, what the same link pasted again finds, what the
retention sweep reads and marks (`features/downloads/`)."""

from functools import lru_cache
lazy from datetime import datetime

from ofmhelpers.db.repositories import DownloadRepository
lazy from ofmhelpers.schemas.downloads import Download, DownloadedFile, RemovedFile


@lru_cache(maxsize=1)
def _repository() -> DownloadRepository:
    return DownloadRepository()


def record(
    user_id: int, job_id: str, files: list[DownloadedFile], now: datetime
) -> None:
    """The files `job_id` holds on disk now, each at its position (a replay
    updates the rows it finds), and nothing after the last of them."""
    _repository().record(user_id, job_id, files, now)


def drop_from(job_id: str, position: int) -> None:
    """The job's rows at `position` and after."""
    _repository().drop_from(job_id, position)


def of_job(job_id: str) -> list[Download]:
    return _repository().of_job(job_id)


def get(job_id: str, position: int) -> Download | None:
    return _repository().get(job_id, position)


def newest_of(user_id: int, urls: list[str]) -> list[Download]:
    """For each of `urls` `user_id` downloaded before: the files of the
    newest job that fetched it. Their own only, never a set of people."""
    return _repository().newest_of(user_id, urls)


def stored_anywhere(urls: list[str]) -> list[Download]:
    """What the server already holds of `urls`, whoever downloaded it: for
    each, the files of the newest job that still has them all."""
    return _repository().stored_anywhere(urls)


def file_shared(download_id: int, file_path: str) -> bool:
    return _repository().file_shared(download_id, file_path)


def touch(job_id: str, url: str, now: datetime) -> None:
    """The link was asked for again."""
    _repository().touch(job_id, url, now)


def removed_in(job_ids: list[str]) -> list[RemovedFile]:
    """The files of these jobs the sweep removed on purpose."""
    return _repository().removed_in(job_ids)


def by_asset(user_id: int, digest: str) -> list[Download]:
    """`user_id`'s removed files whose asset-store copy was `digest`'s."""
    return _repository().by_asset(user_id, digest)


def idle(before: datetime, after_id: int, limit: int) -> list[Download]:
    """Stored files not asked for or used since `before`, by id."""
    return _repository().idle(before, after_id, limit)


def set_asset_path(download_id: int, asset_path: str) -> None:
    _repository().set_asset_path(download_id, asset_path)


def note_use(download_id: int, used_at: datetime) -> None:
    _repository().note_use(download_id, used_at)


def mark_removed(download_id: int, idle_since: datetime, now: datetime) -> bool:
    """False when it was asked for or used meanwhile: nothing to delete."""
    return _repository().mark_removed(download_id, idle_since, now)


def put_back(download_id: int) -> None:
    """Stored after all: the sweep could not delete its file."""
    _repository().put_back(download_id)


def asset_shared(download_id: int, asset_path: str) -> bool:
    return _repository().asset_shared(download_id, asset_path)
