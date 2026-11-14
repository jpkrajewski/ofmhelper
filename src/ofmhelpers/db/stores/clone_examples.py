"""/clone's SOP examples: the rows. What an example shows and keeps on disk
is `features/clone/examples.py`."""

from functools import lru_cache

from ofmhelpers.db.repositories import CloneExampleRepository
lazy from ofmhelpers.schemas.clone_examples import CloneExample

__all__ = ["add", "every", "get", "remove"]


@lru_cache(maxsize=1)
def _repository() -> CloneExampleRepository:
    return CloneExampleRepository()


def every() -> list[CloneExample]:
    return _repository().every()


def get(example_id: int) -> CloneExample | None:
    return _repository().get(example_id)


def add(job_id: str, title: str, prompt: str) -> CloneExample:
    return _repository().add(job_id, title, prompt)


def remove(example_id: int) -> bool:
    return _repository().remove(example_id)
