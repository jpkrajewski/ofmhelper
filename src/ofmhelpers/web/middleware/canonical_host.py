from urllib.parse import urlsplit

from starlette.datastructures import URL, Headers
from starlette.responses import RedirectResponse
lazy from starlette.types import ASGIApp, Receive, Scope, Send

from ofmhelpers.config import settings


def _bare(host: str) -> str:
    return host.removeprefix("www.")


def canonical_redirect(base_url: str, host: str, url: URL) -> str | None:
    base = urlsplit(base_url)
    canonical = (base.hostname or "").lower()
    host = host.split(":", 1)[0].lower()
    if not canonical or host == canonical or _bare(host) != _bare(canonical):
        return None
    return str(url.replace(scheme=base.scheme or url.scheme, netloc=base.netloc))


class CanonicalHostMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            target = canonical_redirect(
                settings.web.base_url,
                Headers(scope=scope).get("host", ""),
                URL(scope=scope),
            )
            if target is not None:
                status = 301 if scope["method"] in ("GET", "HEAD") else 308
                await RedirectResponse(target, status_code=status)(scope, receive, send)
                return
        await self.app(scope, receive, send)
