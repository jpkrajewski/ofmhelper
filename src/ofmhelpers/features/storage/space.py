import hashlib

from pydantic import BaseModel, SkipValidation, ValidationError

from ofmhelpers.cache import delete_text, get_text, set_text
from ofmhelpers.config import settings
from ofmhelpers.db.drive_folders.schema import folder_url
from ofmhelpers.db.users import store as users
from ofmhelpers.db.users.store import refresh_token
from ofmhelpers.features.storage.constants import storage_constants
from ofmhelpers.features.storage.models import _Read
from ofmhelpers.features.storage.schema import DriveSpace
from ofmhelpers.integrations.signin import SignInError
from ofmhelpers.integrations.storage import (
    RECONNECT,
    DriveAuthError,
    DriveError,
    DriveFullError,
    FolderGoneError,
    StorageClient,
)
from ofmhelpers.log import get_logger

logger = get_logger(__name__)


def _key(user_id: int, token: str) -> str:
    digest = hashlib.sha256(token.encode()).hexdigest()[:16]
    return f"drive-space:{user_id}:{digest}"


def _readable(n: int) -> str:
    c = storage_constants()
    gb, mb = c.bytes_per_gb, c.bytes_per_mb
    return f"{n / gb:.1f} GB" if n >= gb else f"{n / mb:.0f} MB"


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


def _token_key(user_id: int) -> str | None:
    token = refresh_token(user_id)
    return _key(user_id, token) if token else None


class StorageServiceSpace(BaseModel, frozen=True, arbitrary_types_allowed=True):
    client: SkipValidation[StorageClient] | None
    user_id: int
    read_live: bool

    def cached(self) -> DriveSpace | None:
        key = _token_key(self.user_id)
        found = _cached(key) if key else None
        return self._space(found) if found is not None else None

    def _ask(self, drive: StorageClient) -> _Read:
        try:
            return _Read(storage=drive.storage())
        except DriveAuthError:
            logger.warning(
                "drive space of %s: Google refused the sign-in",
                self.user_id,
                exc_info=True,
            )
            return _Read(reconnect=True)
        except (DriveError, SignInError) as exc:
            logger.warning(
                "drive space of %s not read: %s", self.user_id, exc, exc_info=True
            )
            return _Read()

    def drive_space(self) -> DriveSpace | None:
        key = _token_key(self.user_id)
        if key is None or self.client is None:
            return None
        found = _cached(key)
        if found is None:
            found = self._ask(self.client)
            _remember(key, found)
        return self._space(found)

    def _space(self, found: _Read) -> DriveSpace | None:
        if found.reconnect:
            return DriveSpace(
                reconnect=True,
                folder_url=self._folder(),
                buy_url=storage_constants().buy_space_url,
            )
        free = found.storage.free if found.storage else None
        if free is None:
            return None
        c = storage_constants()
        floor = settings.clone_links.drive_min_free_mb * c.bytes_per_mb
        return DriveSpace(
            free_text=_readable(free),
            free_mb=free // c.bytes_per_mb,
            low=free < floor,
            folder_url=self._folder(),
            buy_url=c.buy_space_url,
        )

    def _folder(self) -> str:
        user = None if self.read_live else users.get_user(self.user_id)
        root = user.drive_root_id if user else None
        return folder_url(root) if root else storage_constants().my_drive_url

    def upload_error(self, exc: Exception) -> str:
        if isinstance(exc, DriveFullError):
            token = refresh_token(self.user_id)
            if token:
                delete_text(_key(self.user_id, token))
            return storage_constants().drive_full
        if isinstance(exc, DriveAuthError):
            token = refresh_token(self.user_id)
            if token:
                _remember(_key(self.user_id, token), _Read(reconnect=True))
            return RECONNECT
        if isinstance(exc, FolderGoneError):
            return storage_constants().folder_missing
        return storage_constants().no_answer
