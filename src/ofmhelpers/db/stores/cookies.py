from functools import lru_cache

from ofmhelpers.db.encrypted import VaultError
from ofmhelpers.db.repositories import CookieRepository
from ofmhelpers.db.stores.watched_profiles import watchers
from ofmhelpers.log import get_logger
lazy from ofmhelpers.schemas.cookies import StoredCookies

logger = get_logger(__name__)

__all__ = [
    "for_user",
    "for_watched",
    "remove",
    "stored",
    "write",
]


@lru_cache(maxsize=1)
def _repository() -> CookieRepository:
    return CookieRepository()


def _readable(user_id: int) -> StoredCookies | None:
    try:
        return _repository().get(user_id)
    except VaultError as exc:
        logger.warning("user %s: cookies unreadable: %s", user_id, exc, exc_info=True)
        return None


def for_user(user_id: int | None) -> str | None:
    if user_id is None:
        return None
    row = _readable(user_id)
    return row.cookies if row else None


def for_watched(username: str) -> str | None:
    holder = _repository().first_holder(watchers(username))
    return for_user(holder)


def stored(user_id: int) -> StoredCookies | None:
    return _readable(user_id)


def write(user_id: int, text: str, file_name: str) -> float:
    return _repository().save(user_id, text, file_name)


def remove(user_id: int) -> bool:
    removed = _repository().delete(user_id)
    if removed:
        logger.info("user %s removed their cookies", user_id)
    return removed
