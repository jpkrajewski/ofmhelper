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

from __future__ import annotations

from functools import lru_cache

from ofmhelpers.config import settings
from ofmhelpers.downloaders.cookies import CookieFileError, summarize
from ofmhelpers.log import get_logger
from ofmhelpers.phones import cookie_sites
from ofmhelpers.web.db.repositories import CookieRepository
from ofmhelpers.web.schemas import SavedCookies
from ofmhelpers.web.stores.watched_profiles import watchers

logger = get_logger(__name__)

__all__ = ["CookieFileError", "for_user", "for_watched", "remove", "save", "saved"]


@lru_cache(maxsize=1)
def _repository() -> CookieRepository:
    return CookieRepository()


def for_user(user_id: int | None) -> str | None:
    """`user_id`'s cookies.txt text, or None (no user, none uploaded)."""
    if user_id is None:
        return None
    row = _repository().get(user_id)
    return row["cookies"] if row else None


def for_watched(username: str) -> str | None:
    """The cookies a watched profile's shared scan uses: the earliest-added
    watcher's who has any, else None."""
    holder = _repository().first_holder(watchers(username))
    return for_user(holder)


def saved(user_id: int) -> SavedCookies | None:
    """What /cookies shows of `user_id`'s cookies (no value), or None."""
    row = _repository().get(user_id)
    if row is None:
        return None
    try:
        summary = summarize(row["cookies"], cookie_sites())
    except CookieFileError:
        # Only a file stored before uploads were checked (the old shared
        # one, moved by alembic) can get here.
        logger.warning("stored cookies of user %s are not readable", user_id)
        return None
    return SavedCookies(
        file_name=row["file_name"], uploaded_at=row["uploaded_at"], summary=summary
    )


def save(user_id: int, payload: bytes, file_name: str) -> SavedCookies:
    """Stores (or replaces) `user_id`'s cookies. Raises CookieFileError,
    with a message for the uploader, when `payload` is not a cookies.txt
    holding at least one cookie, or is too big."""
    limit = settings.downloaders.cookies_max_bytes
    if len(payload) > limit:
        mb = 1024 * 1024
        msg = (
            f"That file is {len(payload) / mb:.1f} MB; "
            f"a cookies.txt may be up to {limit / mb:.0f} MB"
        )
        raise CookieFileError(msg)
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        msg = "Not a cookies.txt file: it is not text"
        raise CookieFileError(msg) from exc
    summary = summarize(text, cookie_sites())
    uploaded_at = _repository().save(user_id, text, file_name)
    logger.info(
        "user %s uploaded cookies (%d cookies, Instagram login: %s)",
        user_id,
        summary.cookies,
        summary.instagram_login,
    )
    return SavedCookies(file_name=file_name, uploaded_at=uploaded_at, summary=summary)


def remove(user_id: int) -> bool:
    removed = _repository().delete(user_id)
    if removed:
        logger.info("user %s removed their cookies", user_id)
    return removed
