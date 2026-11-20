from ofmhelpers.config import settings
from ofmhelpers.db.stores import cookies
from ofmhelpers.integrations.downloaders.cookies import CookieFileError, summarize
from ofmhelpers.log import get_logger
from ofmhelpers.schemas import SavedCookies
from ofmhelpers.schemas.phone_catalog import cookie_sites

logger = get_logger(__name__)

__all__ = ["CookieFileError", "check_size", "save", "saved"]


def saved(user_id: int) -> SavedCookies | None:
    found = cookies.stored(user_id)
    if found is None:
        return None
    try:
        summary = summarize(found.cookies, cookie_sites())
    except CookieFileError:
        logger.warning(
            "stored cookies of user %s are not readable", user_id, exc_info=True
        )
        return None
    return SavedCookies(
        file_name=found.file_name, uploaded_at=found.uploaded_at, summary=summary
    )


def check_size(size: int) -> None:
    limit = settings.downloaders.cookies_max_bytes
    if size > limit:
        mb = 1024 * 1024
        msg = (
            f"That file is {size / mb:.1f} MB; "
            f"a cookies.txt may be up to {limit / mb:.0f} MB"
        )
        raise CookieFileError(msg)


def save(user_id: int, payload: bytes, file_name: str) -> SavedCookies:
    check_size(len(payload))
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        msg = "Not a cookies.txt file: it is not text"
        raise CookieFileError(msg) from exc
    summary = summarize(text, cookie_sites())
    uploaded_at = cookies.write(user_id, text, file_name)
    logger.info(
        "user %s uploaded cookies (%d cookies, Instagram login: %s)",
        user_id,
        summary.cookies,
        summary.instagram_login,
    )
    return SavedCookies(file_name=file_name, uploaded_at=uploaded_at, summary=summary)
