from redis.exceptions import RedisError

from ofmhelpers.cache import get_redis
from ofmhelpers.config import settings
from ofmhelpers.log import get_logger

logger = get_logger(__name__)


def _key(batch: str) -> str:
    return f"clone-links:stop:{batch}"


def stop_batch(batch: str) -> None:
    get_redis().set(_key(batch), "1", ex=settings.aigen.resume_max_age_s)
    logger.info("clone-links: batch %s stopped", batch)


def stopped(batch: str | None) -> bool:
    if not batch:
        return False
    try:
        return bool(get_redis().exists(_key(batch)))
    except RedisError:
        logger.warning("clone-links: stop of batch %s unreadable", batch, exc_info=True)
        return False
