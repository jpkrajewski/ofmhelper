"""Where a person's events go, per sender: their own Telegram chat
(`db/repositories/event_targets.py`)."""

from functools import lru_cache

from ofmhelpers.db.repositories import EventTargetRepository

__all__ = ["of", "remove", "save"]


@lru_cache(maxsize=1)
def _repository() -> EventTargetRepository:
    return EventTargetRepository()


def of(user_id: int, sender: str) -> str | None:
    return _repository().of(user_id, sender)


def save(user_id: int, sender: str, target: str) -> bool:
    """False: the person was deleted meanwhile, nothing saved."""
    return _repository().save(user_id, sender, target)


def remove(user_id: int, sender: str) -> bool:
    return _repository().remove(user_id, sender)
