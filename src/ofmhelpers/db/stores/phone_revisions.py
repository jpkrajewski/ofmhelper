"""A person's phones as they were: the History list, going back to a
revision, and the states the backup file holds. The database writes the
revisions itself (`db/repositories/phone_revisions.py`)."""

from functools import lru_cache

from ofmhelpers.db.repositories import PhoneRevisionRepository
lazy from ofmhelpers.schemas.phone_revisions import (
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
    """None when the revision is not `owner`'s. /drive's cache of who has
    which login is dropped: logins came and went."""
    done = _repository().restore(revision_id, owner)
    if done is not None:
        from ofmhelpers.db.stores import drive_folders

        drive_folders.logins_changed()
    return done


def history() -> list[PhoneState]:
    return _repository().history()


def newest_id() -> int:
    return _repository().newest_id()


def load(state: PhoneState) -> int | None:
    return _repository().load(state)
