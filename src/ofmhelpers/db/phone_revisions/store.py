from functools import lru_cache

from ofmhelpers.db.phone_revisions.repository import PhoneRevisionRepository
lazy from ofmhelpers.db.drive_folders import store as drive_folders
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
    done = _repository().restore(revision_id, owner)
    if done is not None:
        drive_folders.logins_changed()
    return done


def history() -> list[PhoneState]:
    return _repository().history()


def newest_id() -> int:
    return _repository().newest_id()


def load(state: PhoneState) -> int | None:
    return _repository().load(state)
