"""
ofmhelpers/web/stores/clone_ai_checks.py

AI checks kept for good (features/clone_links/review.py runs and saves them): the
verdict, the model's reasoning, the tokens it used, why a check has none
(`error`, `error_kind`, the raw `error_detail`) and everything to debug one.
Every `CloneAiCheck` field is a column: `save` writes the whole model.
"""

from functools import lru_cache

from ofmhelpers.db.repositories import CloneAiCheckRepository
lazy from ofmhelpers.schemas.clone_ai_checks import CloneAiCheck, StoredCloneAiCheck


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
    return _repository().add(
        {
            **check.model_dump(mode="json"),
            "user_id": user_id,
            "job_id": job_id,
            "file_index": file_index,
        }
    )


def get(check_id: int) -> StoredCloneAiCheck | None:
    return _repository().get(check_id)


def for_jobs(job_ids: list[str]) -> list[StoredCloneAiCheck]:
    """Every check of these jobs' results, oldest first."""
    return _repository().for_jobs(job_ids)
