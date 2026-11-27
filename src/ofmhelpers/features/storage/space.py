# Copyright (C) 2026 jpkrajewski

import logging

from pydantic import BaseModel, SkipValidation, ValidationError

from ofmhelpers.cache import delete_text, get_text, set_text
from ofmhelpers.config import settings
from ofmhelpers.db.drive_folders.schema import folder_url
from ofmhelpers.db.users import store as users
from ofmhelpers.features.storage.constants import StorageConstants
from ofmhelpers.features.storage.models import _Read
from ofmhelpers.features.storage.schema import DriveSpace
from ofmhelpers.integrations.signin import SignInError
from ofmhelpers.integrations.storage import (
    DriveAuthError,
    DriveError,
    DriveFullError,
    FolderGoneError,
    StorageClient,
)

logger = logging.getLogger(__name__)


def _key(user_id: int, session: str) -> str:
    return f"drive-space:{user_id}:{session}"


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


class StorageServiceSpace(BaseModel, frozen=True, arbitrary_types_allowed=True):
    client: SkipValidation[StorageClient] | None
    user_id: int
    read_live: bool
    constants: StorageConstants
    session: str = ""

    def _session_key(self) -> str | None:
        return _key(self.user_id, self.session) if self.session else None

    def _readable(self, n: int) -> str:
        gb, mb = self.constants.bytes_per_gb, self.constants.bytes_per_mb
        return f"{n / gb:.1f} GB" if n >= gb else f"{n / mb:.0f} MB"

    def cached(self) -> DriveSpace | None:
        key = self._session_key()
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
                "drive space of %s not read: %s",
                self.user_id,
                exc,
                exc_info=True,
            )
            return _Read()

    def drive_space(self) -> DriveSpace | None:
        key = self._session_key()
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
                buy_url=self.constants.buy_space_url,
            )
        free = found.storage.free if found.storage else None
        if free is None:
            return None
        c = self.constants
        floor = settings.clone_links.drive_min_free_mb * c.bytes_per_mb
        return DriveSpace(
            free_text=self._readable(free),
            free_mb=free // c.bytes_per_mb,
            low=free < floor,
            folder_url=self._folder(),
            buy_url=c.buy_space_url,
        )

    def _folder(self) -> str:
        user = None if self.read_live else users.get_user(self.user_id)
        root = user.drive_root_id if user else None
        return folder_url(root) if root else self.constants.my_drive_url

    def upload_error(self, exc: Exception) -> str:
        if isinstance(exc, DriveFullError):
            key = self._session_key()
            if key:
                delete_text(key)
            return self.constants.drive_full
        if isinstance(exc, DriveAuthError):
            key = self._session_key()
            if key:
                _remember(key, _Read(reconnect=True))
            return str(exc)
        if isinstance(exc, FolderGoneError):
            return self.constants.folder_missing
        return self.constants.no_answer
