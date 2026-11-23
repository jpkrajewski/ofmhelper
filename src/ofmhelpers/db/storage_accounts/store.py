from functools import lru_cache

from ofmhelpers.db.encrypted import VaultError
from ofmhelpers.db.storage_accounts.repository import StorageAccountRepository
from ofmhelpers.log import get_logger
lazy from ofmhelpers.db.storage_accounts.models import StorageAccount
lazy from ofmhelpers.db.users.schema import StorageProvider

logger = get_logger(__name__)


@lru_cache(maxsize=1)
def _repository() -> StorageAccountRepository:
    return StorageAccountRepository()


def save(
    user_id: int,
    provider: StorageProvider,
    refresh_token: str,
    account_id: str,
    email: str,
) -> None:
    _repository().save(user_id, provider, refresh_token, account_id, email)


def refresh_token(user_id: int, provider: StorageProvider) -> str | None:
    try:
        return _repository().refresh_token(user_id, provider)
    except VaultError as exc:
        logger.warning(
            "user %s: %s token unreadable: %s", user_id, provider, exc, exc_info=True
        )
        return None


def get(user_id: int, provider: StorageProvider) -> StorageAccount | None:
    return _repository().get(user_id, provider)
