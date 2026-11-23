# Copyright (C) 2026 jpkrajewski

from functools import lru_cache

from ofmhelpers.db.phone_revisions.repository import PhoneRevisionRepository
lazy from ofmhelpers.db.phone_revisions.schema import (
    PhoneRevision,
    PhoneState,
    Restored,
)

__all__ = ["history", "load", "newest_id", "page", "restore"]


@lru_cache(maxsize=1)
def _repository() -> PhoneRevisionRepository:
    return PhoneRevisionRepository()


def page(owner: int, limit: int) -> list[PhoneRevision]:
    return _repository().page(owner, limit)


def restore(revision_id: int, owner: int) -> Restored | None:
    return _repository().restore(revision_id, owner)


def history() -> list[PhoneState]:
    return _repository().history()


def newest_id() -> int:
    return _repository().newest_id()


def load(state: PhoneState) -> int | None:
    return _repository().load(state)
