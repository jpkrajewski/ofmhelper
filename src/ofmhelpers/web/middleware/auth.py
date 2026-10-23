"""Who is signed in, and which paths may be reached without being anyone.

One signed session cookie (Starlette's SessionMiddleware) carries the
signed-in user (`SessionUser`: id, email, role); one middleware checks it on
every request. New routers need zero changes -- they're protected the moment
they're mounted on `app`, because this runs before routing.

Every request re-reads the user (cached, `stores.users.get_user`): a user
removed or deactivated on /users is signed out at their next click, and a
role change applies at once. A session holds the user's `credential_stamp`
from when it signed in: once the password changes, every other session of
theirs is signed out (the one that changed it is re-remembered). A session
from before users existed (it holds a role, no user) is sent back to /login.

The allowlist is deployment config (`settings.web.public_paths` /
`public_prefixes`), not literals in here.
"""

from fastapi import HTTPException
from opentelemetry import trace
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import (
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)

from ofmhelpers.config import settings
from ofmhelpers.log import get_logger
from ofmhelpers.web.schemas import SessionUser, User
from ofmhelpers.web.stores.users import get_user

logger = get_logger(__name__)

_KEYS = ("user_id", "email", "role")
_STAMP = "stamp"


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if self.is_public(request.url.path):
            return await call_next(request)
        current = self.session_user(request)
        user = await run_in_threadpool(get_user, current.id) if current else None
        reason = self._refused(request, current, user)
        # (`current`/`user` restated: the reason already covers them.)
        if reason is not None or current is None or user is None:
            # A session that ends by itself says why (2026-10-23: one ended
            # between two clicks on dev and nothing told which check it was).
            if reason != "no session" or request.cookies.get("ofm_session"):
                logger.warning(
                    "auth: %s refused (%s), user %s",
                    request.url.path,
                    reason,
                    current.id if current else "-",
                )
            request.session.clear()
            return self._to_login(request)
        if user.role != current.role or user.email != current.email:
            self.remember(request, user)
        # Whose request this trace is (OpenTelemetry's enduser.*).
        trace.get_current_span().set_attributes(
            {
                "enduser.id": str(user.id),
                "enduser.email": user.email,
                "enduser.role": str(user.role),
            }
        )
        return await call_next(request)

    @staticmethod
    def _refused(
        request: Request, current: SessionUser | None, user: User | None
    ) -> str | None:
        """Why this session may not go on, or None when it may."""
        if current is None:
            return "no session"
        if user is None:
            return "user gone"
        if not user.active:
            return "deactivated"
        if request.session.get(_STAMP, "") != user.credential_stamp:
            return "password changed"
        return None

    def _to_login(self, request: Request) -> Response:
        """Not signed in and hitting a protected route: to /login,
        remembering where they were headed so login can send them back."""
        next_url = request.url.path
        if request.url.query:
            next_url += f"?{request.url.query}"
        if self.is_fetch(request):
            return JSONResponse(
                {"detail": "Session expired", "login_url": f"/login?next={next_url}"},
                status_code=401,
            )
        if "dl" in request.query_params:
            # A Download click: the browser would save the login page under
            # the video's name (2026-10-23: a 2.5 KB "VID_8121.mp4" that
            # "crashed"). A 401 shows as a failed download instead.
            return PlainTextResponse(
                "Signed out: sign in again, then download", status_code=401
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

    # --- the session ---------------------------------------------------------

    @staticmethod
    def remember(request: Request, user: User) -> None:
        """Writes `user` into the session (keeping the rest of it)."""
        request.session.update(
            {
                "user_id": user.id,
                "email": user.email,
                "role": str(user.role),
                _STAMP: user.credential_stamp,
            }
        )

    @classmethod
    def sign_in(cls, request: Request, user: User) -> None:
        """A fresh session for `user`: nothing of a previous one carries
        over (no session fixation)."""
        request.session.clear()
        cls.remember(request, user)

    @staticmethod
    def session_user(request: Request) -> SessionUser | None:
        session = request.session
        if not all(k in session for k in _KEYS):
            return None
        return SessionUser(
            id=session["user_id"], email=session["email"], role=session["role"]
        )

    @classmethod
    def user(cls, request: Request) -> SessionUser:
        """The signed-in user of a protected route (the middleware let it
        through, so there is one)."""
        found = cls.session_user(request)
        if found is None:
            raise HTTPException(status_code=401, detail="Not signed in")
        return found

    @classmethod
    def is_admin(cls, request: Request) -> bool:
        found = cls.session_user(request)
        return found is not None and found.is_admin

    @classmethod
    def require_admin(cls, request: Request) -> None:
        """FastAPI dependency: 403s anyone not an admin.

        Use as a router-level `dependencies=[Depends(AuthMiddleware.require_admin)]`
        for whole pages users shouldn't reach at all (file-manager, action-log,
        /users). For routes where users can view but only admins can mutate,
        check `is_admin` inline per-route instead.
        """
        if not cls.is_admin(request):
            raise HTTPException(status_code=403, detail="Admins only")

    @classmethod
    def actor(cls, request: Request) -> str:
        """What a job this request starts stores as its `actor`: the user's id."""
        return cls.user(request).actor

    @classmethod
    def owner_id(cls, request: Request) -> int:
        """Who a row this request creates (a phone, an account, a watched
        profile) belongs to: the signed-in user."""
        return cls.user(request).id

    @classmethod
    def scope(cls, request: Request) -> int:
        """Whose rows this request may see and change: the signed-in user's
        own, admins included -- nobody reads another person's phones,
        accounts, reels or Drive folders. An admin's extra power is managing
        people (/users) and the admin-only tools, not other users' work.
        "Everyone's" (owner None in the stores) is the background sweeps'."""
        return cls.user(request).id
