from collections.abc import Iterator
from contextlib import contextmanager

from redis.exceptions import LockError, RedisError

from ofmhelpers.cache.redis import get_redis
from ofmhelpers.log import get_logger

logger = get_logger(__name__)


@contextmanager
def locked(key: str, wait_s: int) -> Iterator[None]:
    lock = get_redis().lock(f"lock:{key}", timeout=wait_s, blocking_timeout=wait_s)
    try:
        held = bool(lock.acquire())
    except RedisError:
        logger.warning("lock %s unavailable", key, exc_info=True)
        held = False
    if not held:
        logger.warning("lock %s not taken: going ahead unlocked", key)
    try:
        yield
    finally:
        if held:
            try:
                lock.release()
            except RedisError, LockError:
                logger.warning("lock %s not released", key, exc_info=True)
