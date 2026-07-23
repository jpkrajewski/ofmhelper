"""
Simple single-password auth for the whole app.

Design: one password (APP_PASSWORD env var), one signed session cookie
(via Starlette's SessionMiddleware), one middleware that checks it on
every request. New routers need zero changes -- they're protected the
moment they're mounted on `app`, because this runs before routing.
"""

import os

from fastapi import HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import RedirectResponse

# The two roles the shared passwords resolve to -- centralized here so every
# role check in the app compares against these instead of a hand-typed
# literal (a typo'd literal silently fails a role check instead of erroring).
ROLE_ADMIN = "admin"
ROLE_VA = "va"

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


def check_password(candidate: str) -> str | None:
    """Returns the matching role ("admin" / "va"), or None if it matches neither.

    Two shared passwords instead of one -- there's still no per-user accounts,
    just two roles, each gating which kie.ai key gets pre-filled (see
    get_kie_api_key below).
    """
    import hmac

    admin_password = os.environ[
        "APP_PASSWORD_ADMIN"
    ]  # required -- fail loudly if unset
    va_password = os.environ["APP_PASSWORD_VA"]  # required -- fail loudly if unset

    if hmac.compare_digest(candidate, admin_password):
        return ROLE_ADMIN
    if hmac.compare_digest(candidate, va_password):
        return ROLE_VA
    return None


def get_kie_api_key(request: Request) -> str:
    """Pre-fill value for the kie.ai API key field, based on the logged-in role.

    Optional by design (os.getenv, not os.environ) -- an unset var just means
    the field starts empty and the user pastes a key in manually, same as
    before roles existed.
    """
    role = request.session.get("role")
    env_var = "KIE_AI_API_KEY_ADMIN" if role == ROLE_ADMIN else "KIE_AI_API_KEY_VA"
    return os.getenv(env_var, "")


def require_admin(request: Request) -> None:
    """FastAPI dependency: 403s any request whose session role isn't admin.

    Use as a router-level `dependencies=[Depends(require_admin)]` for whole
    pages VAs shouldn't reach at all (file-manager, action-log). For routes
    where VAs can view but only admins can mutate, check role inline per-route
    instead -- see routers/todo.py.
    """
    if request.session.get("role") != ROLE_ADMIN:
        raise HTTPException(status_code=403, detail="Admins only")
