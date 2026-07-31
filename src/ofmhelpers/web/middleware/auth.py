"""Simple single-password auth for the whole app: who you are, and which paths
you may reach without being anyone.

Design: one password per role (APP_PASSWORD_ADMIN/_VA), one signed session
cookie (via Starlette's SessionMiddleware), one middleware that checks it on
every request. New routers need zero changes -- they're protected the moment
they're mounted on `app`, because this runs before routing.

The allowlist and the role names are deployment config
(`settings.web.public_paths` / `public_prefixes` / `role_admin` / `role_va`),
not literals in here.
"""

import hmac

from fastapi import HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse

from ofmhelpers.config import settings


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if self.is_public(request.url.path) or request.session.get("authenticated"):
            return await call_next(request)

        # Not logged in and hitting a protected route -- bounce to /login,
        # remembering where they were headed so login can send them back.
        next_url = request.url.path
        if request.url.query:
            next_url += f"?{request.url.query}"

        if self.is_fetch(request):
            return JSONResponse(
                {"detail": "Session expired", "login_url": f"/login?next={next_url}"},
                status_code=401,
            )
        return RedirectResponse(url=f"/login?next={next_url}", status_code=303)

    @staticmethod
    def is_public(path: str) -> bool:
        s = settings.web
        if path in s.public_paths:
            return True
        return any(path.startswith(p) for p in s.public_prefixes)

    @staticmethod
    def is_fetch(request: Request) -> bool:
        """True for a fetch()/XHR call, false for a browser page navigation.

        Matters because `fetch` follows a 303 transparently: an expired session
        made every background call resolve to the *login page's HTML* with
        status 200, which the JS then tried to parse as JSON (or, worse,
        injected into the page). Those callers need a machine-readable 401
        instead so they can redirect the whole tab -- see static/js/session.js.

        Detected by ruling navigation *out* rather than trying to enumerate
        what a fetch looks like: `sec-fetch-mode: navigate` is sent by every
        browser for a top-level page load (typing a URL, clicking a link,
        submitting a non-JS form) and never for fetch()/XHR. Only when that
        header is absent entirely (a non-browser client, an old browser) do we
        fall back to the `x-requested-with` / `accept: application/json` hints
        -- a plain document request stays a redirect, which is what keeps the
        login bounce working for real navigations.
        """
        mode = request.headers.get("sec-fetch-mode")
        if mode:
            return mode != "navigate"
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return True
        accept = request.headers.get("accept", "")
        return "application/json" in accept and "text/html" not in accept

    @staticmethod
    def check_password(candidate: str) -> str | None:
        """Returns the matching role (admin / va), or None if it matches neither.

        Two shared passwords instead of one -- there's still no per-user
        accounts, just two roles, each gating which kie.ai key gets pre-filled
        (see web/api_keys.py).
        """
        s = settings.web
        if s.app_password_admin is None:
            msg = "APP_PASSWORD_ADMIN"
            raise KeyError(msg)  # required -- fail loudly if unset
        if s.app_password_va is None:
            msg = "APP_PASSWORD_VA"
            raise KeyError(msg)  # required -- fail loudly if unset

        if hmac.compare_digest(candidate, s.app_password_admin):
            return s.role_admin
        if hmac.compare_digest(candidate, s.app_password_va):
            return s.role_va
        return None

    @staticmethod
    def require_admin(request: Request) -> None:
        """FastAPI dependency: 403s any request whose session role isn't admin.

        Use as a router-level `dependencies=[Depends(AuthMiddleware.require_admin)]`
        for whole pages VAs shouldn't reach at all (file-manager, action-log).
        For routes where VAs can view but only admins can mutate, check role
        inline per-route instead -- see routers/workflow/todo.py.
        """
        if request.session.get("role") != settings.web.role_admin:
            raise HTTPException(status_code=403, detail="Admins only")
