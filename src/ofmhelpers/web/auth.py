"""
Simple single-password auth for the whole app.

Design: one password (APP_PASSWORD env var), one signed session cookie
(via Starlette's SessionMiddleware), one middleware that checks it on
every request. New routers need zero changes -- they're protected the
moment they're mounted on `app`, because this runs before routing.
"""

from fastapi import HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse

from ofmhelpers.config import settings

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
PUBLIC_PREFIXES = (
    "/static/",  # css/js/images, if you serve any
    "/approve/",  # magic-link asset approval -- see routers/workflow/approve.py
)


def is_public(path: str) -> bool:
    if path in PUBLIC_PATHS:
        return True
    return any(path.startswith(p) for p in PUBLIC_PREFIXES)


def is_fetch(request: Request) -> bool:
    """True for a fetch()/XHR call, false for a browser page navigation.

    Matters because `fetch` follows a 303 transparently: an expired session
    made every background call resolve to the *login page's HTML* with
    status 200, which the JS then tried to parse as JSON (or, worse, injected
    into the page). Those callers need a machine-readable 401 instead so they
    can redirect the whole tab -- see static/js/session.js.

    Detected by ruling navigation *out* rather than trying to enumerate what
    a fetch looks like: `sec-fetch-mode: navigate` is sent by every browser
    for a top-level page load (typing a URL, clicking a link, submitting a
    non-JS form) and never for fetch()/XHR. Only when that header is absent
    entirely (a non-browser client, an old browser) do we fall back to the
    `x-requested-with` / `accept: application/json` hints -- a plain
    document request stays a redirect, which is what keeps the login bounce
    working for real navigations.
    """
    mode = request.headers.get("sec-fetch-mode")
    if mode:
        return mode != "navigate"
    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return True
    accept = request.headers.get("accept", "")
    return "application/json" in accept and "text/html" not in accept


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if is_public(request.url.path) or request.session.get("authenticated"):
            return await call_next(request)

        # Not logged in and hitting a protected route -- bounce to /login,
        # remembering where they were headed so login can send them back.
        next_url = request.url.path
        if request.url.query:
            next_url += f"?{request.url.query}"

        if is_fetch(request):
            return JSONResponse(
                {"detail": "Session expired", "login_url": f"/login?next={next_url}"},
                status_code=401,
            )
        return RedirectResponse(url=f"/login?next={next_url}", status_code=303)


def check_password(candidate: str) -> str | None:
    """Returns the matching role ("admin" / "va"), or None if it matches neither.

    Two shared passwords instead of one -- there's still no per-user accounts,
    just two roles, each gating which kie.ai key gets pre-filled (see
    get_kie_api_key below).
    """
    import hmac

    s = settings.web
    if s.app_password_admin is None:
        msg = "APP_PASSWORD_ADMIN"
        raise KeyError(msg)  # required -- fail loudly if unset
    if s.app_password_va is None:
        msg = "APP_PASSWORD_VA"
        raise KeyError(msg)  # required -- fail loudly if unset
    admin_password = s.app_password_admin
    va_password = s.app_password_va

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
    s = settings.web
    key = s.kie_ai_api_key_admin if role == ROLE_ADMIN else s.kie_ai_api_key_va
    return key or ""


def get_elevenlabs_api_key() -> str:
    """Pre-fill value for the ElevenLabs API key field.

    Same optional-by-design contract as get_kie_api_key, but role-blind:
    `ELEVENLABS_API_KEY` is one workspace key, so admin and VA get the same
    one, and an unset var just leaves the field empty.
    """
    return settings.web.elevenlabs_api_key or ""


def require_admin(request: Request) -> None:
    """FastAPI dependency: 403s any request whose session role isn't admin.

    Use as a router-level `dependencies=[Depends(require_admin)]` for whole
    pages VAs shouldn't reach at all (file-manager, action-log). For routes
    where VAs can view but only admins can mutate, check role inline per-route
    instead -- see routers/workflow/todo.py.
    """
    if request.session.get("role") != ROLE_ADMIN:
        raise HTTPException(status_code=403, detail="Admins only")
