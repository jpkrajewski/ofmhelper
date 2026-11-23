# Copyright (C) 2026 jpkrajewski

import hashlib
from collections.abc import Callable
from functools import partial
from types import MappingProxyType
from typing import Protocol

from pydantic import BaseModel, SkipValidation

from ofmhelpers.config import settings
from ofmhelpers.db.storage_accounts import store as storage_accounts
from ofmhelpers.db.users import store as users
from ofmhelpers.db.users.schema import StorageProvider
from ofmhelpers.features.storage.constants import StorageConstants
from ofmhelpers.features.storage.models import BotRoot, StorageAccess
from ofmhelpers.features.storage.service import StorageService
from ofmhelpers.integrations.storage import PerThreadDrive, StorageClient
from ofmhelpers.integrations.storage.dropbox import (
    connect_as as dropbox_connect_as,
)
from ofmhelpers.integrations.storage.dropbox import dropbox_constants
from ofmhelpers.integrations.storage.gdrive import (
    connect,
    connect_as,
    service_account_email,
)
from ofmhelpers.log import get_logger

logger = get_logger(__name__)


class _Connect(Protocol):
    def __call__(self, refresh_token: str, *, interactive: bool) -> StorageClient: ...


class _Provider(BaseModel, frozen=True, arbitrary_types_allowed=True):
    token: SkipValidation[Callable[[int], str | None]]
    connect: SkipValidation[_Connect]
    constants: SkipValidation[Callable[[], StorageConstants]]


def _google_constants() -> StorageConstants:
    return StorageConstants()


def _dropbox_constants() -> StorageConstants:
    return StorageConstants.model_validate(dropbox_constants(), from_attributes=True)


def _dropbox_token(user_id: int) -> str | None:
    return storage_accounts.refresh_token(user_id, StorageProvider.DROPBOX)


_providers = MappingProxyType(
    {
        StorageProvider.GDRIVE: _Provider(
            token=users.refresh_token,
            connect=lambda token, *, interactive: connect_as(
                token,
                interactive=interactive,
            ),
            constants=_google_constants,
        ),
        StorageProvider.DROPBOX: _Provider(
            token=_dropbox_token,
            connect=dropbox_connect_as,
            constants=_dropbox_constants,
        ),
    },
)


def _provider_of(user_id: int) -> StorageProvider:
    user = users.get_user(user_id)
    return user.storage_provider if user else StorageProvider.GDRIVE


def _session(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()[:16]


def read_live() -> bool:
    return settings.gdrive.live_read(settings.infra.production)


def storage_owner(user_id: int) -> int:
    user = users.get_user(user_id)
    return user.owner_id if user and user.owner_id else user_id


def _bot_root(user_id: int) -> BotRoot | None:
    s = settings.gdrive
    if not (s.service_account and s.root_folder_id):
        return None
    if users.refresh_token(user_id):
        return None
    email = service_account_email()
    if email is None:
        logger.warning("drive: OFM_GDRIVE_SERVICE_ACCOUNT is not a usable key")
        return None
    agency = users.get_user(user_id)
    if agency is None or not agency.is_password_account:
        return None
    return BotRoot(folder_id=s.root_folder_id, email=email)


def access(user_id: int, interactive: bool) -> StorageAccess:
    provider = _provider_of(user_id)
    entry = _providers[provider]
    live, constants = read_live(), entry.constants()
    token = entry.token(user_id)
    if token:
        person = PerThreadDrive(partial(entry.connect, token, interactive=interactive))
        return StorageAccess(
            owner_id=user_id,
            client=person,
            constants=constants,
            read_live=live,
            session=_session(token),
        )
    bot = _bot_root(user_id) if provider is StorageProvider.GDRIVE else None
    if bot is None:
        return StorageAccess(
            owner_id=user_id,
            client=None,
            constants=constants,
            read_live=live,
        )
    user = users.get_user(user_id)
    if user is not None and user.drive_root_id != bot.folder_id:
        users.set_drive_root(user_id, bot.folder_id)
    return StorageAccess(
        owner_id=user_id,
        client=PerThreadDrive(partial(connect, interactive=interactive)),
        constants=constants,
        bot=bot,
        read_live=live,
    )


def storage_of(user_id: int, interactive: bool) -> StorageService:
    return StorageService.compose(access(user_id, interactive=interactive))


def saved_storage(user_id: int) -> StorageService:
    return StorageService.compose(
        StorageAccess(
            owner_id=user_id,
            client=None,
            constants=_providers[_provider_of(user_id)].constants(),
            read_live=read_live(),
        ),
    )
