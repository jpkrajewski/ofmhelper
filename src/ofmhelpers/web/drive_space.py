"""Whether a person's Google Drive has room for the videos the app puts in
it (/clone-links' reviews, /clone's Upload to Drive).

A full Drive refuses every upload (dev 2026-10-25: "The user's Drive storage
quota has been exceeded"), and the VA only found out card by card. Every
signed-in page asks `GET /account/drive-space` in the background
(static/js/drive-alert.js: a red pop-up, chime and desktop notification
through notify.js, like the posting window); the space is Drive's own
`storageQuota`, cached in Redis for `OFM_CLONE_LINKS_DRIVE_SPACE_TTL_S`. An
upload Drive refuses for space says so in a few words (`upload_error`) and
forgets the cached answer, so the next page shows the pop-up.
"""

from __future__ import annotations

from pydantic import BaseModel

from ofmhelpers.cache import delete_text, get_text, set_text
from ofmhelpers.config import settings
from ofmhelpers.gdrive import DriveError, connect_as, read_live_mode
from ofmhelpers.gdrive.models import DriveStorage, folder_url
from ofmhelpers.log import get_logger
from ofmhelpers.signin import SignInError
from ofmhelpers.web.stores import users
from ofmhelpers.web.stores.users import refresh_token

logger = get_logger(__name__)

DRIVE_FULL = "Google Drive is full"
_QUOTA_SIGNS = ("storage quota", "storagequotaexceeded")


# Google One's storage plans: where a person buys more Drive space.
BUY_SPACE_URL = "https://one.google.com/storage"
_MY_DRIVE_URL = "https://drive.google.com/drive/my-drive"


class DriveSpace(BaseModel):
    """What the page shows: the free space as people read it, whether it is
    under `OFM_CLONE_LINKS_DRIVE_MIN_FREE_MB`, the folder the videos go in
    (their ALL ACCOUNTS root; My Drive when not known, or on dev reading
    the live folders, where "DEV - ALL ACCOUNTS" sits) and where to buy
    more space."""

    free_text: str
    low: bool
    folder_url: str
    buy_url: str = BUY_SPACE_URL


def _key(user_id: int) -> str:
    return f"clone-links:drive-space:{user_id}"


_MB = 1_000_000
_GB = 1_000_000_000


def _readable(n: int) -> str:
    return f"{n / _GB:.1f} GB" if n >= _GB else f"{n / _MB:.0f} MB"


def _storage(user_id: int) -> DriveStorage | None:
    raw = get_text(_key(user_id))
    if raw is not None:
        try:
            return DriveStorage.model_validate_json(raw)
        except ValueError:
            logger.warning("drive space of %s: cached value unreadable", user_id)
    token = refresh_token(user_id)
    if not token:
        return None
    try:
        found = connect_as(token, interactive=True).storage()
    except (DriveError, SignInError) as exc:
        logger.warning("drive space of %s not read: %s", user_id, exc)
        return None
    set_text(
        _key(user_id), found.model_dump_json(), settings.clone_links.drive_space_ttl_s
    )
    return found


def drive_space(user_id: int) -> DriveSpace | None:
    """`user_id`'s Drive space; None without Google connected, with no
    limit, or when Drive could not be asked (the page then says nothing)."""
    found = _storage(user_id)
    free = found.free if found else None
    if free is None:
        return None
    floor = settings.clone_links.drive_min_free_mb * _MB
    return DriveSpace(
        free_text=_readable(free), low=free < floor, folder_url=_folder(user_id)
    )


def _folder(user_id: int) -> str:
    user = None if read_live_mode() else users.get_user(user_id)
    root = user.drive_root_id if user else None
    return folder_url(root) if root else _MY_DRIVE_URL


def upload_error(user_id: int, exc: Exception) -> str:
    """A failed upload's reason as the card shows it: "Google Drive is full"
    for Drive's quota refusal (its cached space forgotten), else Drive's own
    message."""
    text = str(exc)
    if any(sign in text.lower() for sign in _QUOTA_SIGNS):
        delete_text(_key(user_id))
        return DRIVE_FULL
    return text
