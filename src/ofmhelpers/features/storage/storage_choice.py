from ofmhelpers.db.users import store as users
from ofmhelpers.features.storage import jobs as storage_jobs
from ofmhelpers.log import get_logger
lazy from ofmhelpers.db.users.schema import StorageProvider

logger = get_logger(__name__)


def use(user_id: int, provider: StorageProvider) -> None:
    current = users.get_user(user_id)
    if current is None or current.storage_provider != provider:
        users.set_storage_provider(user_id, provider)
    users.set_drive_root(user_id, None)
    try:
        storage_jobs.queue_sync(user_id)
    except Exception:
        logger.exception(
            "storage: queueing the sync after switching %s failed", user_id
        )
