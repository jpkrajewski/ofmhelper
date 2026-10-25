"""
ofmhelpers/web/stores/clone_from_links.py

/clone-links results on their way to Drive (web/clone_links/review.py
starts and runs them; the page shows each card's), each with its check
(`stores/clone_ai_checks.py`).
"""

from __future__ import annotations

import time
from functools import lru_cache

from ofmhelpers.config import settings
from ofmhelpers.web.db.repositories import CloneFromLinkRepository
from ofmhelpers.web.schemas.clone_ai_checks import CloneFromLink
from ofmhelpers.web.stores import clone_ai_checks


@lru_cache(maxsize=1)
def _repository() -> CloneFromLinkRepository:
    return CloneFromLinkRepository()


def start(job_id: str, file_index: int, user_id: int) -> int | None:
    """The review's id, started; None when the file is reviewed already or
    under review (a failed one starts again, and one running longer than
    `settings.clone_links.review_stale_s`: cut off)."""
    stale_before = time.time() - settings.clone_links.review_stale_s
    return _repository().start(job_id, file_index, user_id, stale_before)


def get(review_id: int) -> CloneFromLink | None:
    found = _repository().get(review_id)
    return CloneFromLink.model_validate(found) if found else None


def for_jobs(job_ids: list[str]) -> list[CloneFromLink]:
    """Every review of these jobs' results, each with its check."""
    rows = [CloneFromLink.model_validate(r) for r in _repository().for_jobs(job_ids)]
    checks = {c.id: c for c in clone_ai_checks.for_jobs(job_ids)}
    return [
        r.model_copy(update={"check": checks.get(r.check_id)}) if r.check_id else r
        for r in rows
    ]


def checked(review_id: int, check_id: int, folder_name: str) -> None:
    _repository().checked(review_id, check_id, folder_name)


def finish(
    review_id: int,
    *,
    drive_file_id: str | None = None,
    drive_url: str | None = None,
    error: str | None = None,
) -> None:
    """Done (with the Drive file), or failed with `error`."""
    _repository().finish(
        review_id, drive_file_id=drive_file_id, drive_url=drive_url, error=error
    )
