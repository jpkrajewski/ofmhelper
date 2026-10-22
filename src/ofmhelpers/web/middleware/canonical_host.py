"""One public host: `www.` of `APP_BASE_URL`'s host (or the bare host when
the base is `www.`) redirects there, path and query kept.

Cookies are per host, and the sign-in callback is built from
`APP_BASE_URL`: a Google connect started on www.ascendglobal.cash set its
state cookie there, came back to ascendglobal.cash without it and failed
("took too long or was not started here"; prod, 2026-10-22). Sessions split
the same way. Any other host (loopback, the container IP the deploy's
/health probe uses) is left alone; nothing happens without `APP_BASE_URL`.
"""

from urllib.parse import urlsplit

from starlette.datastructures import URL, Headers
from starlette.responses import RedirectResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from ofmhelpers.config import settings


def _bare(host: str) -> str:
    return host.removeprefix("www.")


def canonical_redirect(base_url: str, host: str, url: URL) -> str | None:
    """Where a request for `url` on `host` belongs, or None to serve it."""
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
                # 308 keeps a POST a POST (and its body) on the way over.
                status = 301 if scope["method"] in ("GET", "HEAD") else 308
                await RedirectResponse(target, status_code=status)(scope, receive, send)
                return
        await self.app(scope, receive, send)
