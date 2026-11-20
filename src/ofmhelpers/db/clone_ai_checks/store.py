from functools import lru_cache

from ofmhelpers.db.clone_ai_checks.repository import CloneAiCheckRepository
lazy from ofmhelpers.db.clone_ai_checks.schema import CloneAiCheck, StoredCloneAiCheck


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
    return _repository().for_jobs(job_ids)
