# Copyright (C) 2026 jpkrajewski

import secrets

from starlette.datastructures import MutableHeaders
lazy from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ofmhelpers.config import settings
from ofmhelpers.web.templates_config import NONCE_KEY

FRAME_HEADERS = {
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "frame-ancestors 'none'",
}
_DOC_PATHS = frozenset({"/docs", "/redoc", "/docs/oauth2-redirect"})
_POLICY = (
    "frame-ancestors 'none'; script-src 'self' 'nonce-{nonce}'; "
    "object-src 'none'; base-uri 'self'"
)


class FrameGuardMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        nonce = secrets.token_urlsafe(16)
        scope.setdefault("state", {})[NONCE_KEY] = nonce
        docs = scope["path"] in _DOC_PATHS and not settings.infra.production

        async def guarded(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.setdefault("X-Frame-Options", FRAME_HEADERS["X-Frame-Options"])
                headers.setdefault(
                    "Content-Security-Policy",
                    FRAME_HEADERS["Content-Security-Policy"]
                    if docs
                    else _POLICY.format(nonce=nonce),
                )
            await send(message)

        await self.app(scope, receive, guarded)
