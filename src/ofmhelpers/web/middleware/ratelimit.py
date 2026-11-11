"""How often you may ask: fixed-window rate limiting, counters and enforcement
in one place.

Three users, all here:

- `WriteRateLimitMiddleware` -- a blunt per-IP ceiling on POST/PUT/PATCH/DELETE
  so no write endpoint can be hammered, without tuning a limit per route.
- `record_failed_login` / `login_blocked` / `clear_failed_logins`, called by
  routers/auth.py's /login route. Only *failed* logins are counted, so a
  legitimate user is never locked out by their own successful traffic, and a
  correct password clears the counter.
- `record_refused_webhook` / `webhook_blocked`, called by routers/webhooks.py.
  Only deliveries failing the signature count: a real one is never turned away.

The counters live on the one Redis connection in the repo
(`ofmhelpers.cache.get_redis`) -- shared state, so the limit holds across every
uvicorn worker instead of per-process.

Redis failures fail *open* (request allowed, warning logged). A broken broker
already takes the app down -- turning it into a total lockout of a working
login form would be strictly worse than briefly losing the ceiling.

The client identity is `request.client.host`. Prod sits behind the host's
nginx, which appends the peer to X-Forwarded-For; uvicorn (`--proxy-headers
--forwarded-allow-ips`, Dockerfile) trusts only loopback and Docker's private
range, so `client.host` is the address nginx saw -- never "*", which would
take the client-written leftmost entry and let anyone pick their bucket.
"""

from fastapi.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
lazy from starlette.requests import Request

from ofmhelpers.cache.rate_limit import LOGIN_BUCKET as _LOGIN_BUCKET
from ofmhelpers.cache.rate_limit import WRITE_BUCKET as _WRITE_BUCKET
from ofmhelpers.cache.rate_limit import (
    clear_failed_logins,
    client_id,
    login_blocked,
    record_failed_login,
    record_refused_webhook,
    webhook_blocked,
)
from ofmhelpers.cache.rate_limit import counter_key as _key
from ofmhelpers.cache.rate_limit import hit as _hit
from ofmhelpers.config import settings
from ofmhelpers.log import get_logger

__all__ = [
    "WRITE_METHODS",
    "_LOGIN_BUCKET",
    "WriteRateLimitMiddleware",
    "_key",
    "clear_failed_logins",
    "client_id",
    "login_blocked",
    "record_failed_login",
    "record_refused_webhook",
    "webhook_blocked",
]

logger = get_logger(__name__)

WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class WriteRateLimitMiddleware(BaseHTTPMiddleware):
    """Deliberately generous: this is an anti-hammering backstop, not a quota
    -- a real user uploading a batch of reference files must never see a 429."""

    async def dispatch(self, request: Request, call_next):
        s = settings.web
        # A provider's webhooks are signed, and a mass message delivers hundreds.
        if (
            not s.rate_limit_enabled
            or request.method not in WRITE_METHODS
            or request.url.path.startswith("/events/")
        ):
            return await call_next(request)

        identifier = client_id(request)
        # The counters use the synchronous Redis client, so this would block
        # the event loop for every mutating request -- fine against a local
        # Redis, not fine on a latency spike or a connect timeout. Passing the
        # identifier rather than the request is what lets _hit run off-loop.
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
