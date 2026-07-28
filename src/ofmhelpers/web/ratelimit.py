"""
Fixed-window rate limiting, on the Redis connection the RQ queue already
owns (web/queue.py) -- shared state, so the limit holds across every uvicorn
worker instead of per-process.

Two users:

- `LoginRateLimitMiddleware`'s counterpart in routers/auth.py: only *failed*
  logins are counted, so a legitimate user is never locked out by their own
  successful traffic, and a correct password clears the counter.
- `WriteRateLimitMiddleware`: a blunt per-IP ceiling on POST/PUT/PATCH/DELETE
  so no write endpoint can be hammered, without tuning a limit per route.

Redis failures fail *open* (request allowed, warning logged). A broken
broker already takes the app down -- turning it into a total lockout of a
working login form would be strictly worse than briefly losing the ceiling.

The client identity is `request.client.host`. Prod publishes the app port
directly (see docker-compose.yml), so that is the real peer address. If a
reverse proxy is ever put in front, uvicorn needs `--proxy-headers
--forwarded-allow-ips=<proxy>` or every client collapses into one bucket.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi.concurrency import run_in_threadpool
from redis.exceptions import RedisError
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from ofmhelpers.config import settings
from ofmhelpers.log import get_logger
from ofmhelpers.web.queue import get_redis

if TYPE_CHECKING:
    from starlette.requests import Request

logger = get_logger(__name__)

WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

_LOGIN_BUCKET = "login_failures"
_WRITE_BUCKET = "write"


def client_id(request: Request) -> str:
    client = request.client
    return client.host if client is not None else "unknown"


def _key(bucket: str, identifier: str) -> str:
    return f"ratelimit:{bucket}:{identifier}"


def _hit(bucket: str, identifier: str, window_s: int) -> int:
    """Count one event in the current window; returns the new count (0 when
    Redis is unreachable, i.e. treat as under the limit)."""
    key = _key(bucket, identifier)
    try:
        redis = get_redis()
        count = int(redis.incr(key))
        # Fixed window: the TTL starts with the first event and the whole
        # counter disappears at the end of it. `nx=True` means this only sets
        # a TTL when the key has none, so it is safe to call on every hit --
        # and it must be, not just when count == 1. If the INCR lands and the
        # EXPIRE then fails (broker blip, failover), a count-gated expire is
        # never retried: the key lives forever with no TTL and that IP is
        # locked out of /login permanently, which is the opposite of the
        # fail-open this module promises. Calling it every time repairs a
        # missing TTL on the next request instead.
        redis.expire(key, window_s, nx=True)
    except RedisError:
        logger.warning("rate limit counter unavailable, allowing", exc_info=True)
        return 0
    else:
        return count


def _count(bucket: str, identifier: str) -> int:
    try:
        raw = get_redis().get(_key(bucket, identifier))
    except RedisError:
        logger.warning("rate limit counter unavailable, allowing", exc_info=True)
        return 0
    return int(raw) if raw is not None else 0


def _clear(bucket: str, identifier: str) -> None:
    try:
        get_redis().delete(_key(bucket, identifier))
    except RedisError:
        logger.warning("rate limit counter unavailable, not cleared", exc_info=True)


def login_blocked(request: Request) -> bool:
    """True once this client has burned its failed-login allowance. Read-only
    -- the counter is only advanced by record_failed_login()."""
    s = settings.web
    if not s.rate_limit_enabled:
        return False
    return _count(_LOGIN_BUCKET, client_id(request)) >= s.login_max_failures


def record_failed_login(request: Request) -> None:
    _hit(_LOGIN_BUCKET, client_id(request), settings.web.login_failure_window_s)


def clear_failed_logins(request: Request) -> None:
    _clear(_LOGIN_BUCKET, client_id(request))


class WriteRateLimitMiddleware(BaseHTTPMiddleware):
    """Per-IP ceiling on mutating requests. Deliberately generous: this is an
    anti-hammering backstop, not a quota -- a real user uploading a batch of
    reference files must never see a 429."""

    async def dispatch(self, request: Request, call_next):
        s = settings.web
        if not s.rate_limit_enabled or request.method not in WRITE_METHODS:
            return await call_next(request)

        identifier = client_id(request)
        # get_redis() is the synchronous client, so this would block the event
        # loop for every mutating request -- fine against a local Redis, not
        # fine on a latency spike or a connect timeout.
        count = await run_in_threadpool(
            _hit, _WRITE_BUCKET, identifier, s.write_rate_limit_window_s
        )
        if count > s.write_rate_limit_requests:
            logger.warning(
                "write rate limit exceeded: %s %s from %s (%d in %ds)",
                request.method,
                request.url.path,
                identifier,
                count,
                s.write_rate_limit_window_s,
            )
            return JSONResponse(
                {"detail": "Too many requests"},
                status_code=429,
                headers={"Retry-After": str(s.write_rate_limit_window_s)},
            )
        return await call_next(request)
