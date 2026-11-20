import json
from functools import cache, wraps
from typing import Protocol, TypeVar, get_type_hints
lazy from collections.abc import Callable

from pydantic import TypeAdapter, ValidationError
from redis.exceptions import RedisError
lazy from redis import Redis

from ofmhelpers.cache import get_redis
from ofmhelpers.config import settings
from ofmhelpers.log import get_logger

T = TypeVar("T")

logger = get_logger(__name__)


class RepositoryCacheLike(Protocol):
    def get_or_set(self, method: str, args: tuple, loader: Callable[[], T]) -> T: ...

    def bump(self) -> None: ...


class RepositoryCache:
    def __init__(
        self, redis: Redis | None, namespace: str, ttl_s: int | None = None
    ) -> None:
        self._fixed = redis
        self._namespace = namespace
        self._fixed_ttl_s = ttl_s

    @property
    def _ttl_s(self) -> int:
        if self._fixed_ttl_s is not None:
            return self._fixed_ttl_s
        return settings.infra.cache_ttl_s

    @property
    def _redis(self) -> Redis:
        return self._fixed if self._fixed is not None else get_redis()

    def _version(self) -> int:
        v = self._redis.get(f"cache:{self._namespace}:v")
        return int(v) if v is not None else 0

    def _key(self, method: str, args: tuple) -> str:
        parts = ":".join(str(a) for a in args)
        return f"cache:{self._namespace}:{self._version()}:{method}:{parts}"

    def get_or_set(self, method: str, args: tuple, loader: Callable[[], T]) -> T:
        try:
            key = self._key(method, args)
            cached = self._redis.get(key)
        except RedisError:
            logger.warning(
                "cache %s unavailable, %s read from the database",
                self._namespace,
                method,
                exc_info=True,
            )
            return loader()
        if cached is not None:
            return json.loads(cached)
        value = loader()
        try:
            self._redis.set(key, json.dumps(value), ex=self._ttl_s)
        except RedisError:
            logger.warning(
                "cache %s unavailable, %s not stored",
                self._namespace,
                method,
                exc_info=True,
            )
        return value

    def bump(self) -> None:
        try:
            self._redis.incr(f"cache:{self._namespace}:v")
        except RedisError:
            logger.exception(
                "cache %s not invalidated: stale reads until their TTL",
                self._namespace,
            )


def _cache_key_args(args: tuple, kwargs: dict) -> tuple:
    if not kwargs:
        return args
    return args + tuple(f"{k}={v}" for k, v in sorted(kwargs.items()))


@cache
def _returned[T](method: Callable[..., T]) -> TypeAdapter[T]:
    return TypeAdapter(get_type_hints(method)["return"])


def cached[T](method: Callable[..., T]) -> Callable[..., T]:

    @wraps(method)
    def wrapper(self, *args, **kwargs):
        cache_args = _cache_key_args(args, kwargs)
        returned = _returned(method)
        stored = self._cache.get_or_set(
            method.__name__,
            cache_args,
            lambda: returned.dump_python(method(self, *args, **kwargs), mode="json"),
        )
        try:
            return returned.validate_python(stored)
        except ValidationError:
            logger.warning(
                "cache %s: stored %s does not fit its model, read from the database",
                self.cache_namespace,
                method.__name__,
                exc_info=True,
            )
            return method(self, *args, **kwargs)

    return wrapper


def invalidates_cache[T](method: Callable[..., T]) -> Callable[..., T]:

    @wraps(method)
    def wrapper(self, *args, **kwargs):
        result = method(self, *args, **kwargs)
        self._cache.bump()
        return result

    return wrapper


class CachedRepository:
    cache_namespace: str | None = None

    def __init__(self, cache: RepositoryCacheLike | None = None) -> None:
        if self.cache_namespace is None:
            msg = f"{type(self).__name__} must define cache_namespace"
            raise ValueError(msg)
        self._cache: RepositoryCacheLike = cache or RepositoryCache(
            None, self.cache_namespace
        )
