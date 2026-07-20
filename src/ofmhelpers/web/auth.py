"""
Simple single-password auth for the whole app.

Design: one password (APP_PASSWORD env var), one signed session cookie
(via Starlette's SessionMiddleware), one middleware that checks it on
every request. New routers need zero changes -- they're protected the
moment they're mounted on `app`, because this runs before routing.
"""

import os

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import RedirectResponse

# Paths that must stay reachable without being logged in.
# Keep this list short and explicit -- anything not listed is protected,
# which is the safe default direction for an allowlist.
PUBLIC_PATHS = {
    "/login",
    "/health",
}
PUBLIC_PREFIXES = ("/static/",)  # css/js/images, if you serve any


def is_public(path: str) -> bool:
    if path in PUBLIC_PATHS:
        return True
    return any(path.startswith(p) for p in PUBLIC_PREFIXES)


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if is_public(request.url.path) or request.session.get("authenticated"):
            return await call_next(request)

        # Not logged in and hitting a protected route -- bounce to /login,
        # remembering where they were headed so login can send them back.
        next_url = request.url.path
        if request.url.query:
            next_url += f"?{request.url.query}"
        return RedirectResponse(url=f"/login?next={next_url}", status_code=303)


def check_password(candidate: str) -> bool:
    expected = os.environ["APP_PASSWORD"]  # required -- fail loudly if unset
    # constant-time-ish comparison isn't critical here (single shared
    # password, not per-user secrets), but cheap to do properly anyway.
    import hmac

    return hmac.compare_digest(candidate, expected)
