"""
ofmhelpers/web/stores/clone_from_links.py

/clone-links results on their way to Drive (web/clone_links/review.py
starts and runs them; the page shows each card's), each with its check
(`stores/clone_ai_checks.py`). A worker claims a review before it runs it;
what it writes lands only while its claim holds (repository docstring).
"""

from __future__ import annotations

import time
from functools import lru_cache

from ofmhelpers.config import settings
from ofmhelpers.web.db.repositories import CloneFromLinkRepository
from ofmhelpers.web.schemas import Job
from ofmhelpers.web.schemas.clone_ai_checks import CloneFromLink
from ofmhelpers.web.stores import clone_ai_checks


@lru_cache(maxsize=1)
def _repository() -> CloneFromLinkRepository:
    return CloneFromLinkRepository()


def _reviews(rows: list[dict]) -> list[CloneFromLink]:
    return [CloneFromLink.model_validate(r) for r in rows]


def start(job_id: str, file_index: int, user_id: int, path: str | None) -> int | None:
    """The review's id, started for the file at `path`; None when the file
    is reviewed already, under review, or another user's (a failed one
    starts again, and one running or waiting longer than
    `settings.clone_links.review_stale_s`: cut off)."""
    stale_before = time.time() - settings.clone_links.review_stale_s
    return _repository().start(job_id, file_index, user_id, path, stale_before)


def get(review_id: int) -> CloneFromLink | None:
    found = _repository().get(review_id)
    return CloneFromLink.model_validate(found) if found else None


def claim(review_id: int) -> CloneFromLink | None:
    """The review, claimed by the calling worker; None when it is settled
    or another worker holds it."""
    found = _repository().claim(review_id)
    return CloneFromLink.model_validate(found) if found else None


def for_jobs(job_ids: list[str], user_id: int | None = None) -> list[CloneFromLink]:
    """Every review of these jobs' results (only `user_id`'s when given:
    what a page passes), each with its check."""
    rows = _reviews(_repository().for_jobs(job_ids, user_id))
    checks = {c.id: c for c in clone_ai_checks.for_jobs(job_ids)}
    return [
        r.model_copy(update={"check": checks.get(r.check_id)}) if r.check_id else r
        for r in rows
    ]


def checked(review: CloneFromLink, check_id: int | None, folder_name: str) -> bool:
    """`review` (as claimed) checked: uploading into `folder_name`. False
    when the row is no longer its claim's."""
    return _repository().checked(review.id, review.started_at, check_id, folder_name)


def finish(
    review_id: int,
    *,
    claimed: float | None,
    drive_file_id: str | None = None,
    drive_url: str | None = None,
    error: str | None = None,
) -> bool:
    """Done (with the Drive file), or failed with `error`, if the row is
    still as the claim made at `claimed` left it (None: never claimed)."""
    return _repository().finish(
        review_id,
        claimed=claimed,
        drive_file_id=drive_file_id,
        drive_url=drive_url,
        error=None if error is None else str(error),
    )


# --- recovery ------------------------------------------------------------------


def cut_off(stale_before: float) -> list[CloneFromLink]:
    """Running reviews a worker claimed before `stale_before`."""
    return _reviews(_repository().cut_off(stale_before))


def release(review: CloneFromLink) -> bool:
    """`review`'s claim dropped: it waits for a worker again."""
    return _repository().release(review.id, review.started_at)


def failed_since(since: float) -> list[CloneFromLink]:
    """Failed reviews with nothing in Drive, finished after `since`."""
    return _reviews(_repository().failed_since(since))


def reopen(review: CloneFromLink) -> bool:
    """A failed `review` running again (its saved check kept: an upload
    only); False when it changed meanwhile."""
    return _repository().reopen(review.id, review.finished_at)


def unreviewed_jobs(task: str, since: float) -> list[Job]:
    """`task`'s done jobs finished after `since` that have no review."""
    return [
        Job.model_validate(row) for row in _repository().unreviewed_jobs(task, since)
    ]
