"""No page of the app shows inside another site's frame.

A signed-in page framed by someone else's site could be clicked through
unseen (clickjacking). Until 2026-11-18 only the session cookie's
SameSite=Lax stood in the way; the security audit asked for the headers
themselves. Nothing of ours frames the app, so every answer says no:
`X-Frame-Options: DENY` for old browsers, CSP `frame-ancestors 'none'` for
current ones. The bridge pages answer outside this stack: `bridge.py`
`_HEADERS` sends the same two.
"""

from starlette.datastructures import MutableHeaders
lazy from starlette.types import ASGIApp, Message, Receive, Scope, Send

_HEADERS = (
    ("X-Frame-Options", "DENY"),
    ("Content-Security-Policy", "frame-ancestors 'none'"),
)


class FrameGuardMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def guarded(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in _HEADERS:
                    headers.setdefault(name, value)
            await send(message)

        await self.app(scope, receive, guarded)
