from functools import partial

from ofmhelpers.config import settings
from ofmhelpers.db.users import store as users
from ofmhelpers.features.storage.models import BotRoot, StorageAccess
from ofmhelpers.features.storage.service import StorageService
from ofmhelpers.integrations.storage import PerThreadDrive
from ofmhelpers.integrations.storage.gdrive import (
    connect,
    connect_as,
    service_account_email,
)
from ofmhelpers.log import get_logger

logger = get_logger(__name__)


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
    live = read_live()
    token = users.refresh_token(user_id)
    if token:
        person = PerThreadDrive(partial(connect_as, token, interactive=interactive))
        return StorageAccess(owner_id=user_id, client=person, read_live=live)
    bot = _bot_root(user_id)
    if bot is None:
        return StorageAccess(owner_id=user_id, client=None, read_live=live)
    user = users.get_user(user_id)
    if user is not None and user.drive_root_id != bot.folder_id:
        users.set_drive_root(user_id, bot.folder_id)
    return StorageAccess(
        owner_id=user_id,
        client=PerThreadDrive(partial(connect, interactive=interactive)),
        bot=bot,
        read_live=live,
    )


def storage_of(user_id: int, interactive: bool) -> StorageService:
    return StorageService.compose(access(user_id, interactive=interactive))


def saved_storage(user_id: int) -> StorageService:
    return StorageService.compose(
        StorageAccess(owner_id=user_id, client=None, read_live=read_live())
    )
