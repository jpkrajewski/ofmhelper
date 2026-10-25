"""Whether a person's Google Drive has room for the videos the app puts in
it (/clone-links' reviews, /clone's Upload to Drive), and whether Google
still takes their sign-in at all.

A full Drive refuses every upload (dev 2026-10-25: "The user's Drive storage
quota has been exceeded"), and the VA only found out card by card. Every
signed-in page asks `GET /account/drive-space` in the background
(static/js/drive-alert.js: a red pop-up, chime and desktop notification
through notify.js, like the posting window); the space is Drive's own
`storageQuota`. Each answer is cached in Redis per person and token: a read
for `OFM_CLONE_LINKS_DRIVE_SPACE_TTL_S`, Google refusing the token
(`reconnect`: the pop-up says Reconnect) as long, Drive not answering for
`OFM_GDRIVE_SPACE_RETRY_S` -- so a failing Drive is asked once a minute,
never on every page view. A new token (Reconnect) is a new key: its first
page asks again. A page that must not wait on Google reads only the cache
(`drive_space(..., ask=False)`).

A failed upload's reason, as a card shows it, is one of a few fixed words by
kind (`upload_error`), never Drive's own text (that goes to the log and the
span). A refusal for space forgets the cached answer, so the next page shows
the pop-up; a refused token is remembered as `reconnect` at once.
"""

from __future__ import annotations

import hashlib

from pydantic import BaseModel, ValidationError

from ofmhelpers.cache import delete_text, get_text, set_text
from ofmhelpers.config import settings
from ofmhelpers.gdrive import (
    RECONNECT,
    DriveAuthError,
    DriveError,
    DriveFullError,
    FolderGoneError,
    connect_as,
    read_live_mode,
)
from ofmhelpers.gdrive.models import DriveStorage, folder_url
from ofmhelpers.log import get_logger
from ofmhelpers.signin import SignInError
from ofmhelpers.web.stores import users
from ofmhelpers.web.stores.users import refresh_token

logger = get_logger(__name__)

# What a card says when an upload failed (`upload_error`).
DRIVE_FULL = "Google Drive is full"
FOLDER_MISSING = "Drive folder missing"
NO_ANSWER = "Google Drive did not answer"


# Google One's storage plans: where a person buys more Drive space.
BUY_SPACE_URL = "https://one.google.com/storage"
_MY_DRIVE_URL = "https://drive.google.com/drive/my-drive"


class DriveSpace(BaseModel):
    """What the page shows: the free space as people read it and in MB,
    whether it is under `OFM_CLONE_LINKS_DRIVE_MIN_FREE_MB`, or `reconnect`
    (Google refused the person's token: no space known); the folder the
    videos go in (their ALL ACCOUNTS root; My Drive when not known, or on
    dev reading the live folders, where "DEV - ALL ACCOUNTS" sits) and where
    to buy more space."""

    free_text: str = ""
    free_mb: int | None = None
    low: bool = False
    reconnect: bool = False
    folder_url: str
    buy_url: str = BUY_SPACE_URL


class _Read(BaseModel):
    """One ask of Google, as cached: the space, or none (Drive did not
    answer; `reconnect` when Google refused the token)."""

    storage: DriveStorage | None = None
    reconnect: bool = False


def _key(user_id: int, token: str) -> str:
    """Per person and token: after a Reconnect (a new token) the old one's
    cached refusal is never the answer."""
    digest = hashlib.sha256(token.encode()).hexdigest()[:16]
    return f"drive-space:{user_id}:{digest}"


_MB = 1_000_000
_GB = 1_000_000_000


def _readable(n: int) -> str:
    return f"{n / _GB:.1f} GB" if n >= _GB else f"{n / _MB:.0f} MB"


def _cached(key: str) -> _Read | None:
    raw = get_text(key)
    if raw is None:
        return None
    try:
        return _Read.model_validate_json(raw)
    except ValidationError:
        logger.warning("drive space: cached value of %s unreadable", key)
        return None


def _remember(key: str, found: _Read) -> None:
    """A read or a refused token for the space TTL; Drive not answering
    only for `space_retry_s`."""
    known = found.storage is not None or found.reconnect
    ttl = (
        settings.clone_links.drive_space_ttl_s
        if known
        else settings.gdrive.space_retry_s
    )
    set_text(key, found.model_dump_json(), ttl)


def _ask(user_id: int, token: str) -> _Read:
    try:
        return _Read(storage=connect_as(token, interactive=True).storage())
    except DriveAuthError:
        logger.warning("drive space of %s: Google refused the sign-in", user_id)
        return _Read(reconnect=True)
    except (DriveError, SignInError) as exc:
        logger.warning("drive space of %s not read: %s", user_id, exc)
        return _Read()


def _read(user_id: int, *, ask: bool) -> _Read | None:
    """The cached answer; on a miss Google's (cached), unless `ask` is
    off. None without Google connected, or on a miss with `ask` off."""
    token = refresh_token(user_id)
    if not token:
        return None
    key = _key(user_id, token)
    found = _cached(key)
    if found is None and ask:
        found = _ask(user_id, token)
        _remember(key, found)
    return found


def drive_space(user_id: int, *, ask: bool = True) -> DriveSpace | None:
    """`user_id`'s Drive space; a `reconnect` one when Google refused their
    token; None without Google connected, with no limit, or when Drive did
    not answer (the page then says nothing). `ask=False`: only what is
    cached -- never waits on Google (a page render)."""
    found = _read(user_id, ask=ask)
    if found is None:
        return None
    if found.reconnect:
        return DriveSpace(reconnect=True, folder_url=_folder(user_id))
    free = found.storage.free if found.storage else None
    if free is None:
        return None
    floor = settings.clone_links.drive_min_free_mb * _MB
    return DriveSpace(
        free_text=_readable(free),
        free_mb=free // _MB,
        low=free < floor,
        folder_url=_folder(user_id),
    )


def _folder(user_id: int) -> str:
    user = None if read_live_mode() else users.get_user(user_id)
    root = user.drive_root_id if user else None
    return folder_url(root) if root else _MY_DRIVE_URL


def upload_error(user_id: int, exc: Exception) -> str:
    """A failed upload's reason as the card shows it, by kind -- never
    Drive's own text (the caller logs it): `DRIVE_FULL` (the cached space
    forgotten, so the next page shows the pop-up), `RECONNECT` (remembered:
    every page says so), `FOLDER_MISSING`, else `NO_ANSWER`."""
    if isinstance(exc, DriveFullError):
        token = refresh_token(user_id)
        if token:
            delete_text(_key(user_id, token))
        return DRIVE_FULL
    if isinstance(exc, DriveAuthError):
        token = refresh_token(user_id)
        if token:
            _remember(_key(user_id, token), _Read(reconnect=True))
        return RECONNECT
    if isinstance(exc, FolderGoneError):
        return FOLDER_MISSING
    return NO_ANSWER
