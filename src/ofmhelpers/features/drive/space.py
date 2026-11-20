import hashlib

from pydantic import BaseModel, ValidationError

from ofmhelpers.cache import delete_text, get_text, set_text
from ofmhelpers.config import settings
from ofmhelpers.db.stores import users
from ofmhelpers.db.stores.users import refresh_token
from ofmhelpers.integrations.gdrive import (
    RECONNECT,
    DriveAuthError,
    DriveError,
    DriveFullError,
    FolderGoneError,
    connect_as,
    read_live_mode,
)
from ofmhelpers.integrations.gdrive.models import DriveStorage, folder_url
from ofmhelpers.integrations.signin import SignInError
from ofmhelpers.log import get_logger

logger = get_logger(__name__)

DRIVE_FULL = "Google Drive is full"
FOLDER_MISSING = "Drive folder missing"
NO_ANSWER = "Google Drive did not answer"


BUY_SPACE_URL = "https://one.google.com/storage"
_MY_DRIVE_URL = "https://drive.google.com/drive/my-drive"


class DriveSpace(BaseModel):
    free_text: str = ""
    free_mb: int | None = None
    low: bool = False
    reconnect: bool = False
    folder_url: str
    buy_url: str = BUY_SPACE_URL


class _Read(BaseModel):
    storage: DriveStorage | None = None
    reconnect: bool = False


def _key(user_id: int, token: str) -> str:
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
        logger.warning("drive space: cached value of %s unreadable", key, exc_info=True)
        return None


def _remember(key: str, found: _Read) -> None:
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
        logger.warning(
            "drive space of %s: Google refused the sign-in", user_id, exc_info=True
        )
        return _Read(reconnect=True)
    except (DriveError, SignInError) as exc:
        logger.warning("drive space of %s not read: %s", user_id, exc, exc_info=True)
        return _Read()


def _read(user_id: int, *, ask: bool) -> _Read | None:
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
