# Copyright (C) 2026 jpkrajewski

from fastapi.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, Response
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
    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        s = settings.web
        if (
            not s.rate_limit_enabled
            or request.method not in WRITE_METHODS
            or request.url.path.startswith("/events/")
        ):
            return await call_next(request)

        identifier = client_id(request)
        count = await run_in_threadpool(
            _hit,
            _WRITE_BUCKET,
            identifier,
            s.write_rate_limit_window_s,
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
