# Copyright (C) 2026 jpkrajewski

import logging
from functools import lru_cache
from threading import Lock

from redis import Redis
from redis.asyncio import Redis as AsyncRedis
from redis.exceptions import RedisError

from ofmhelpers.config import settings

logger = logging.getLogger(__name__)

_manager_lock = Lock()


class _RedisManager:
    def __init__(self) -> None:
        self._lock = Lock()
        self._redis: Redis | None = None
        self._url: str | None = None

    def get(self, url: str) -> Redis:
        with self._lock:
            if self._redis is not None and url == self._url:
                return self._redis
            redis = Redis.from_url(url)
            previous = self._redis
            self._redis = redis
            self._url = url
            if previous is not None:
                previous.close()
            return redis


@lru_cache(maxsize=1)
def _redis_manager() -> _RedisManager:
    return _RedisManager()


def _get_redis_manager() -> _RedisManager:
    with _manager_lock:
        return _redis_manager()


def get_redis() -> Redis:
    return _get_redis_manager().get(settings.infra.redis_url)


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
            "cache read unavailable for %s, treating as miss",
            key,
            exc_info=True,
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
