"""
ofmhelpers/web/stores/cookies.py

Each user's own cookies.txt: the logins their work sends (/cookies uploads,
replaces and removes it; nobody sees or uses another user's).

Whose cookies a piece of work uses:
- a download job: the user who started it (`for_user`);
- a tracked account's scan: the account's owner (`for_user`);
- a watched profile's scan: one scan per username serves every user who
  watches it, so it uses the cookies of the earliest-added watcher who has
  any, else none (`for_watched`). That is the only place one user's login
  makes requests for something another user also sees; the cookies
  themselves never leave the worker.

Values are decrypted here only to be handed to the code that sends them;
they are never cached, logged or put in a job's params (RQ keeps those in
Redis): a job carries the user id and reads the cookies when it runs.
"""

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
    """The stored row; None when the vault key cannot read it: a download runs
    logged out, /cookies offers an upload, never a crash."""
    try:
        return _repository().get(user_id)
    except VaultError as exc:
        logger.warning("user %s: cookies unreadable: %s", user_id, exc)
        return None


def for_user(user_id: int | None) -> str | None:
    """`user_id`'s cookies.txt text, or None (no user, none uploaded)."""
    if user_id is None:
        return None
    row = _readable(user_id)
    return row.cookies if row else None


def for_watched(username: str) -> str | None:
    """The cookies a watched profile's shared scan uses: the earliest-added
    watcher's who has any, else None."""
    holder = _repository().first_holder(watchers(username))
    return for_user(holder)


def stored(user_id: int) -> StoredCookies | None:
    """`user_id`'s stored file, value included (`features/cookies/jar.py`
    shows what it covers); None when there is none or it cannot be read."""
    return _readable(user_id)


def write(user_id: int, text: str, file_name: str) -> float:
    """Stores (or replaces) `user_id`'s cookies; when it was uploaded."""
    return _repository().save(user_id, text, file_name)


def remove(user_id: int) -> bool:
    removed = _repository().delete(user_id)
    if removed:
        logger.info("user %s removed their cookies", user_id)
    return removed
