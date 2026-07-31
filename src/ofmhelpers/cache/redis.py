"""The one Redis connection in this repo, plus the small string key/value
helpers built on it.

The connection is built lazily from settings.infra (never at import), for the
same reason the DB engine is: the worker and the API must bind to whatever
OFM_REDIS_URL is set at runtime, and tests point it at a test broker. Cached
per URL so production reuses one connection pool and a URL change between
tests still rebuilds.

`get_text`/`set_text`/`delete_text` are for **optimisation caches only** --
values a process would happily recompute. They swallow RedisError and report a
miss, so a broker outage degrades to "slow" rather than "broken". Anything that
must not silently lose a write (the RQ queue, the rate-limit counters) uses
`get_redis()` directly and decides its own failure policy.
"""

from __future__ import annotations

from redis import Redis
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


def get_text(key: str) -> str | None:
    """Cached string for `key`, or None on a miss *or* an unreachable broker."""
    try:
        raw = get_redis().get(key)
    except RedisError:
        logger.warning("cache read unavailable for %s, treating as miss", key)
        return None
    if raw is None:
        return None
    return raw.decode() if isinstance(raw, bytes) else str(raw)


def set_text(key: str, value: str, ttl_s: int) -> None:
    try:
        get_redis().set(key, value, ex=ttl_s)
    except RedisError:
        logger.warning("cache write unavailable for %s, not stored", key)


def delete_text(key: str) -> None:
    try:
        get_redis().delete(key)
    except RedisError:
        logger.warning("cache delete unavailable for %s", key)
