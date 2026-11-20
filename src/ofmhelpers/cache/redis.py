from redis import Redis
from redis.asyncio import Redis as AsyncRedis
from redis.exceptions import RedisError

from ofmhelpers.config import settings
from ofmhelpers.log import get_logger

logger = get_logger(__name__)

_redis: Redis | None = None
_redis_url: str | None = None


def get_redis() -> Redis:
    global _redis, _redis_url
    url = settings.infra.redis_url
    if _redis is None or url != _redis_url:
        _redis = Redis.from_url(url)
        _redis_url = url
    return _redis


def subscriber() -> AsyncRedis:
    return AsyncRedis.from_url(settings.infra.redis_url)


def used_bytes() -> int | None:
    try:
        return int(get_redis().info("memory")["used_memory"])
    except RedisError, KeyError, ValueError:
        logger.warning("could not read Redis memory", exc_info=True)
        return None


def get_text(key: str) -> str | None:
    try:
        raw = get_redis().get(key)
    except RedisError:
        logger.warning(
            "cache read unavailable for %s, treating as miss", key, exc_info=True
        )
        return None
    if raw is None:
        return None
    return raw.decode() if isinstance(raw, bytes) else str(raw)


def set_text(key: str, value: str, ttl_s: int) -> None:
    try:
        get_redis().set(key, value, ex=ttl_s)
    except RedisError:
        logger.warning("cache write unavailable for %s, not stored", key, exc_info=True)


def delete_text(key: str) -> None:
    try:
        get_redis().delete(key)
    except RedisError:
        logger.warning("cache delete unavailable for %s", key, exc_info=True)
