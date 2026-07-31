"""
Cache-aside layer for the repositories beside it. One class, injected into
each repository's constructor, so the caching policy lives in exactly one
place instead of being copy-pasted per repository. It sits in `repositories/`
rather than one level up because a repository is its only caller -- nothing
outside this package caches this way.

Invalidation is a version counter per namespace (one per repository), not a
Redis KEYS/SCAN delete: every write calls `bump()`, which does an atomic
INCR, so all previously-cached reads for that repository become unreachable
in O(1) and simply expire off their own TTL later.
"""

from __future__ import annotations

import json
from functools import wraps
from typing import TYPE_CHECKING, Protocol, TypeVar

from ofmhelpers.cache import get_redis
from ofmhelpers.config import settings

if TYPE_CHECKING:
    from collections.abc import Callable

    from redis import Redis

# Safety-net expiry in case a code path ever reads without writing through
# this layer -- invalidation is driven by bump(), not by this TTL.
_DEFAULT_TTL_S = settings.infra.cache_ttl_s

T = TypeVar("T")


class RepositoryCacheLike(Protocol):
    def get_or_set(self, method: str, args: tuple, loader: Callable[[], T]) -> T: ...

    def bump(self) -> None: ...


class RepositoryCache:
    """Cache-aside wrapper around one Redis connection, namespaced per
    repository (e.g. "job", "todo", "model", "approval_token")."""

    def __init__(
        self, redis: Redis, namespace: str, ttl_s: int = _DEFAULT_TTL_S
    ) -> None:
        self._redis = redis
        self._namespace = namespace
        self._ttl_s = ttl_s

    def _version(self) -> int:
        v = self._redis.get(f"cache:{self._namespace}:v")
        return int(v) if v is not None else 0

    def _key(self, method: str, args: tuple) -> str:
        parts = ":".join(str(a) for a in args)
        return f"cache:{self._namespace}:{self._version()}:{method}:{parts}"

    def get_or_set(self, method: str, args: tuple, loader: Callable[[], T]) -> T:
        key = self._key(method, args)
        cached = self._redis.get(key)
        if cached is not None:
            return json.loads(cached)
        value = loader()
        self._redis.set(key, json.dumps(value), ex=self._ttl_s)
        return value

    def bump(self) -> None:
        """Invalidate every cached read for this namespace."""
        self._redis.incr(f"cache:{self._namespace}:v")


class NullCache:
    """No-op cache -- reads always miss, bump() is a no-op. Inject this to
    disable caching (e.g. a test that needs to see writes with no Redis)."""

    def get_or_set(self, method: str, args: tuple, loader: Callable[[], T]) -> T:  # noqa: ARG002
        return loader()

    def bump(self) -> None:
        pass


def _cache_key_args(args: tuple, kwargs: dict) -> tuple:
    if not kwargs:
        return args
    return args + tuple(f"{k}={v}" for k, v in sorted(kwargs.items()))


def cached[T](method: Callable[..., T]) -> Callable[..., T]:
    """Mark a repository read method as cache-aside. The wrapped method's
    body is only run on a cache miss; the cache key is the method name plus
    its call arguments, resolved against `self._cache` (see
    `CachedRepository`)."""

    @wraps(method)
    def wrapper(self, *args, **kwargs):
        cache_args = _cache_key_args(args, kwargs)
        return self._cache.get_or_set(
            method.__name__, cache_args, lambda: method(self, *args, **kwargs)
        )

    return wrapper


def invalidates_cache[T](method: Callable[..., T]) -> Callable[..., T]:
    """Mark a repository write method as cache-invalidating. Runs the method
    and, only if it returns without raising, bumps `self._cache` -- an
    exception (rolled-back transaction) leaves the cache untouched."""

    @wraps(method)
    def wrapper(self, *args, **kwargs):
        result = method(self, *args, **kwargs)
        self._cache.bump()
        return result

    return wrapper


class CachedRepository:
    """Base class for every repository in this package: owns cache
    construction so no subclass repeats the `cache or RepositoryCache(...)`
    boilerplate. Subclasses set `cache_namespace` and use the `@cached` /
    `@invalidates_cache` decorators above -- they never touch `self._cache`
    directly."""

    cache_namespace: str | None = None

    def __init__(self, cache: RepositoryCacheLike | None = None) -> None:
        if self.cache_namespace is None:
            msg = f"{type(self).__name__} must define cache_namespace"
            raise ValueError(msg)
        # Binding the Redis connection here is safe because no repository is
        # built at import any more -- each one comes from an lru_cache'd
        # accessor in web/stores/, so construction happens on first use, by
        # which point OFM_REDIS_URL is whatever the process (or a test
        # fixture) finally set it to.
        self._cache: RepositoryCacheLike = cache or RepositoryCache(
            get_redis(), self.cache_namespace
        )
