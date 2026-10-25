"""
ofmhelpers/web/stores/clone_ai_checks.py

AI checks kept for good (web/clone_links/review.py runs and saves them): the
verdict, the model's reasoning, the tokens it used, why a check has none
(`error`, `error_kind`, the raw `error_detail`) and everything to debug one.
Every `CloneAiCheck` field is a column: `save` writes the whole model.
"""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING

from ofmhelpers.web.db.repositories import CloneAiCheckRepository
from ofmhelpers.web.schemas.clone_ai_checks import StoredCloneAiCheck

if TYPE_CHECKING:
    from ofmhelpers.clone_ai_check import CloneAiCheck


@lru_cache(maxsize=1)
def _repository() -> CloneAiCheckRepository:
    return CloneAiCheckRepository()


def save(
    check: CloneAiCheck,
    *,
    user_id: int | None = None,
    job_id: str | None = None,
    file_index: int | None = None,
) -> StoredCloneAiCheck:
    row = _repository().add(
        {
            **check.model_dump(mode="json"),
            "user_id": user_id,
            "job_id": job_id,
            "file_index": file_index,
        }
    )
    return StoredCloneAiCheck.model_validate(row)


def get(check_id: int) -> StoredCloneAiCheck | None:
    found = _repository().get(check_id)
    return StoredCloneAiCheck.model_validate(found) if found else None


def for_jobs(job_ids: list[str]) -> list[StoredCloneAiCheck]:
    """Every check of these jobs' results, oldest first."""
    return [
        StoredCloneAiCheck.model_validate(r) for r in _repository().for_jobs(job_ids)
    ]
