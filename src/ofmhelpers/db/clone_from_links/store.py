# Copyright (C) 2026 jpkrajewski

import time
from functools import lru_cache

from ofmhelpers.config import settings
from ofmhelpers.db.clone_ai_checks import store as clone_ai_checks
from ofmhelpers.db.clone_from_links.repository import CloneFromLinkRepository
lazy from ofmhelpers.db.clone_ai_checks.schema import CloneFromLink
lazy from ofmhelpers.db.jobs.schema import Job


@lru_cache(maxsize=1)
def _repository() -> CloneFromLinkRepository:
    return CloneFromLinkRepository()


def start(job_id: str, file_index: int, user_id: int, path: str | None) -> int | None:
    stale_before = time.time() - settings.clone_links.review_stale_s
    return _repository().start(job_id, file_index, user_id, path, stale_before)


def get(review_id: int) -> CloneFromLink | None:
    return _repository().get(review_id)


def claim(review_id: int) -> CloneFromLink | None:
    return _repository().claim(review_id)


def for_jobs(job_ids: list[str], user_id: int | None = None) -> list[CloneFromLink]:
    rows = _repository().for_jobs(job_ids, user_id)
    checks = {c.id: c for c in clone_ai_checks.for_jobs(job_ids)}
    return [
        r.model_copy(update={"check": checks.get(r.check_id)}) if r.check_id else r
        for r in rows
    ]


def checked(review: CloneFromLink, check_id: int | None, folder_name: str) -> bool:
    return _repository().checked(review.id, review.started_at, check_id, folder_name)


def finish(
    review_id: int,
    *,
    claimed: float | None,
    drive_file_id: str | None = None,
    drive_url: str | None = None,
    error: str | None = None,
) -> bool:
    return _repository().finish(
        review_id,
        claimed=claimed,
        drive_file_id=drive_file_id,
        drive_url=drive_url,
        error=None if error is None else str(error),
    )


def cut_off(stale_before: float) -> list[CloneFromLink]:
    return _repository().cut_off(stale_before)


def never_claimed(created_before: float) -> list[CloneFromLink]:
    return _repository().never_claimed(created_before)


def release(review: CloneFromLink) -> bool:
    return _repository().release(review.id, review.started_at)


def failed_since(since: float) -> list[CloneFromLink]:
    return _repository().failed_since(since)


def reopen(review: CloneFromLink) -> bool:
    return _repository().reopen(review.id, review.finished_at)


def unreviewed_jobs(task: str, since: float) -> list[Job]:
    return _repository().unreviewed_jobs(task, since)
