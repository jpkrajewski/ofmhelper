# Copyright (C) 2026 jpkrajewski

import json
import logging
from functools import cache, wraps
from typing import Protocol, TypeVar, cast, get_type_hints
lazy from collections.abc import Callable, Sequence

from pydantic import TypeAdapter, ValidationError
from redis.exceptions import RedisError
lazy from redis import Redis

from ofmhelpers.cache import get_redis
from ofmhelpers.config import settings

T = TypeVar("T")

logger = logging.getLogger(__name__)


class RepositoryCacheLike(Protocol):
    def get_or_set(
        self,
        method: str,
        args: Sequence[object],
        loader: Callable[[], T],
    ) -> T: ...

    def bump(self) -> None: ...


class RepositoryCache:
    def __init__(
        self,
        redis: Redis | None,
        namespace: str,
        ttl_s: int | None = None,
        depends_on: Sequence[str] = (),
    ) -> None:
        self._fixed = redis
        self._namespace = namespace
        self._fixed_ttl_s = ttl_s
        self._depends_on = tuple(depends_on)

    @property
    def _ttl_s(self) -> int:
        if self._fixed_ttl_s is not None:
            return self._fixed_ttl_s
        return settings.infra.cache_ttl_s

    @property
    def _redis(self) -> Redis:
        return self._fixed if self._fixed is not None else get_redis()

    def _version(self) -> str:
        names = (self._namespace, *self._depends_on)
        found = self._redis.mget([f"cache:{name}:v" for name in names])
        return "-".join(str(int(v)) if v is not None else "0" for v in found)

    def _key(self, method: str, args: Sequence[object]) -> str:
        parts = json.dumps([str(a) for a in args], separators=(",", ":"))
        return f"cache:v2:{self._namespace}:{self._version()}:{method}:{parts}"

    def get_or_set(
        self,
        method: str,
        args: Sequence[object],
        loader: Callable[[], T],
    ) -> T:
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
            return cast("T", json.loads(cached))
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


def _cache_key_args(args: tuple[object, ...], kwargs: dict[str, object]) -> list[str]:
    return [str(a) for a in args] + [f"{k}={v}" for k, v in sorted(kwargs.items())]


@cache
def _returned[T](method: Callable[..., T]) -> TypeAdapter[T]:
    return TypeAdapter(get_type_hints(method)["return"])


def cached[T](method: Callable[..., T]) -> Callable[..., T]:

    @wraps(method)
    def wrapper(self: CachedRepository, *args: object, **kwargs: object) -> T:
        cache_args = _cache_key_args(args, kwargs)
        returned = _returned(method)
        stored = self._cache.get_or_set(
            method.__name__,
            cache_args,
            lambda: returned.dump_python(method(self, *args, **kwargs), mode="json"),
        )
        try:
            return cast("T", returned.validate_python(stored))
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
    def wrapper(self: CachedRepository, *args: object, **kwargs: object) -> T:
        result = method(self, *args, **kwargs)
        self._cache.bump()
        return result

    return wrapper


class CachedRepository:
    cache_namespace: str | None = None
    cache_depends_on: tuple[str, ...] = ()

    def __init__(self, cache: RepositoryCacheLike | None = None) -> None:
        if self.cache_namespace is None:
            msg = f"{type(self).__name__} must define cache_namespace"
            raise ValueError(msg)
        self._cache: RepositoryCacheLike = cache or RepositoryCache(
            None,
            self.cache_namespace,
            depends_on=self.cache_depends_on,
        )
