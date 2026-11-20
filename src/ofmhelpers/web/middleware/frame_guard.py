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
