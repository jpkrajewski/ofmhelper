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
Logout ends every session of the person (`users.sessions_ended_at`): one
signed in before it (`since`) is refused on any device.

The session slides: Starlette re-sends the cookie only when the session is
written, so every user action (a page opened, anything that is not a read)
writes `seen` and the cookie lives `session_max_age_s` from that moment.
Background polls (fetch GETs) do not count: a tab nobody touches signs out.
A role may sit idle only so long (`features/users/presence.py`
`idle_limit_s`: a VA 2 h, everyone else the cookie's own 5 h): a session
whose last action is older is refused ("idle"), whatever the cookie says.
Each action is also kept on the person, to the minute (`users.last_seen_at`),
which is what records a VA's idle logout (`features/users/idle.py`).
static/js/session.js sends `PUT /session` while the person clicks or types
on a page that makes no request of its own (prod 2026-11: the cookie ended
5 h after sign-in whatever the person did).

Once the session is good, the request's `Viewer` is built
(`features/users/viewing.py`) and kept on `request.state.viewer`: who the
request acts as -- the person signed in, or the person an admin is viewing
as, in which case nothing but a read goes through (`_look_only`; the admin's
own VA excepted, as whom they also act). Every
"who" a route asks for (`web/dependencies.py`) and every "may they"
(`require`, against `auth/access.py`) is answered from it.

The allowlist is deployment config (`settings.web.public_paths` /
`public_prefixes`), not literals in here.
"""

import time
from urllib.parse import quote, urlsplit

from fastapi import HTTPException
from opentelemetry import trace
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import (
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)
lazy from starlette.requests import Request

from ofmhelpers.auth.access import Action, Area, allowed
from ofmhelpers.config import settings
from ofmhelpers.db.stores.users import get_user
from ofmhelpers.features.signin import state as signin_state
from ofmhelpers.features.users import presence
from ofmhelpers.features.users.viewing import viewer_for
from ofmhelpers.log import get_logger
from ofmhelpers.schemas import SessionUser, User
lazy from ofmhelpers.schemas.users import Viewer

logger = get_logger(__name__)

_KEYS = ("user_id", "email", "role")
_STAMP = "stamp"
# When the person last did something (`_acted`): written to slide the cookie.
_SEEN = "seen"
# When this session signed in: one from before the person's last Logout
# (`User.sessions_ended_at`) is out, wherever it is.
_SINCE = "since"
# The keep-alive session.js sends on a click or key press.
SESSION_PATH = "/session"
# The id of the person an admin is viewing as (`view_as` / `stop_viewing`).
_VIEWING = "viewing"
# What goes through while viewing as someone: reads, and the two ways out.
_READS = frozenset({"GET", "HEAD", "OPTIONS"})
STOP_VIEWING_PATH = "/viewing/delete"
_WAYS_OUT = frozenset({STOP_VIEWING_PATH, "/logout", SESSION_PATH})


class AuthMiddleware(BaseHTTPMiddleware):
    # Where a signed-in person lands without a page to return to: the app's
    # home ("/" is the public landing page, never shown to them).
    HOME = signin_state.HOME

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
        if self._acted(request):
            now = time.time()
            request.session[_SEEN] = int(now)
            await run_in_threadpool(presence.seen, user, now)
        viewer = await run_in_threadpool(
            viewer_for, user, request.session.get(_VIEWING)
        )
        if viewer.by is None:
            # Nobody viewed (any more: the person removed, the admin demoted).
            request.session.pop(_VIEWING, None)
        request.state.viewer = viewer
        # Whose request this trace is (OpenTelemetry's enduser.*): the person
        # signed in; `viewing.id` when they look as someone else.
        trace.get_current_span().set_attributes(
            {
                "enduser.id": str(user.id),
                "enduser.email": user.email,
                "enduser.role": str(user.role),
                **({"viewing.id": str(viewer.user.id)} if viewer.by else {}),
            }
        )
        if (
            viewer.read_only
            and request.method not in _READS
            and request.url.path not in _WAYS_OUT
        ):
            return self._look_only(request, viewer)
        return await call_next(request)

    @staticmethod
    def _acted(request: Request) -> bool:
        """Whether the person did this themselves: opened a page, or sent
        anything that is not a read. A script's poll is neither."""
        if request.method not in _READS:
            return True
        return request.headers.get("sec-fetch-mode") == "navigate"

    def _look_only(self, request: Request, viewer: Viewer) -> Response:
        """An admin viewing as someone changes nothing of theirs: every
        write is refused here, in one place, whatever the route. (Their own
        VA is not read only: `features/users/viewing.py`.)"""
        detail = f"You are viewing as {viewer.label}. Go back to your view to do this."
        if self.is_fetch(request):
            return JSONResponse({"detail": detail}, status_code=403)
        return PlainTextResponse(detail, status_code=403)

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
        ended = user.sessions_ended_at
        if ended is not None and request.session.get(_SINCE, 0) <= ended:
            return "logged out"
        # A session from before `since` existed carries neither: it ends
        # with its cookie, as before.
        last = request.session.get(_SEEN) or request.session.get(_SINCE)
        if last and time.time() - last > presence.idle_limit_s(user.role):
            return "idle"
        return None

    def _to_login(self, request: Request) -> Response:
        """Not signed in and hitting a protected route: to /login,
        remembering where they were headed so login can send them back."""
        if self.is_fetch(request):
            # Back to the page that made the call, never the call's own URL
            # (prod 2026-10-27..31: a gallery poll's 401 sent people from
            # login to /clone/gallery/latest, a bare fragment).
            return JSONResponse(
                {
                    "detail": "Session expired",
                    "login_url": self._login_url(self._calling_page(request)),
                },
                status_code=401,
            )
        if "dl" in request.query_params:
            # A Download click: the browser would save the login page under
            # the video's name (2026-10-23: a 2.5 KB "VID_8121.mp4" that
            # "crashed"). A 401 shows as a failed download instead.
            return PlainTextResponse(
                "Signed out: sign in again, then download", status_code=401
            )
        next_url = request.url.path
        if request.url.query:
            next_url += f"?{request.url.query}"
        return RedirectResponse(url=self._login_url(next_url), status_code=303)

    @staticmethod
    def _login_url(next_url: str | None) -> str:
        """/login, sending back to `next_url` (its query kept whole)."""
        return f"/login?next={quote(next_url, safe='/')}" if next_url else "/login"

    @staticmethod
    def _calling_page(request: Request) -> str | None:
        """The page of this site a fetch was made from (its Referer), or
        None when unknown: login then lands on the app's home."""
        referer = urlsplit(request.headers.get("referer", ""))
        if not referer.netloc or referer.netloc != request.url.netloc:
            return None
        return referer.path + (f"?{referer.query}" if referer.query else "")

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
        request.session[_SINCE] = time.time()

    @staticmethod
    def signed_in_at(request: Request) -> float | None:
        """When this session signed in; None for none (or one from before
        that was kept)."""
        return request.session.get(_SINCE)

    @staticmethod
    def session_user(request: Request) -> SessionUser | None:
        session = request.session
        if not all(k in session for k in _KEYS):
            return None
        return SessionUser(
            id=session["user_id"], email=session["email"], role=session["role"]
        )

    @staticmethod
    def view_as(request: Request, user: User) -> None:
        """From the next request on this admin's pages are `user`'s, read
        only (`viewer_for` checks the admin and the person each time)."""
        request.session[_VIEWING] = user.id

    @staticmethod
    def stop_viewing(request: Request) -> None:
        request.session.pop(_VIEWING, None)

    @staticmethod
    def viewer(request: Request) -> Viewer:
        """Who this protected request acts as (the middleware let it
        through, so there is one)."""
        found = getattr(request.state, "viewer", None)
        if found is None:
            raise HTTPException(status_code=401, detail="Not signed in")
        return found

    @classmethod
    def user(cls, request: Request) -> SessionUser:
        """The person the request acts as: the one signed in, or the one an
        admin is viewing as."""
        return cls.viewer(request).user

    @classmethod
    def require(cls, request: Request, what: Area | Action) -> None:
        """Refuses a role the access map (`auth/access.py`) does not let
        open the area or do the action. Used through the `Access` / `Can`
        dependencies of `web/dependencies.py`, never called by a route.

        A page typed or clicked in the browser goes to the person's home
        (303): a VA opening /accounts sees their own start page, not a line
        of JSON saying the page exists and is not theirs (owner 2026-11-14).
        A script's call, and anything that is not a page load, gets the 403."""
        if allowed(cls.viewer(request).user.role, what):
            return
        if request.method == "GET" and cls.is_page_load(request):
            raise HTTPException(status_code=303, headers={"Location": cls.HOME})
        raise HTTPException(status_code=403, detail="Not available to you")

    @staticmethod
    def is_page_load(request: Request) -> bool:
        """A browser loading a page into its tab says so itself."""
        return request.headers.get("sec-fetch-mode") == "navigate"

    @classmethod
    def actor(cls, request: Request) -> str:
        """What a job this request starts stores as its `actor`: the user's id."""
        return cls.user(request).actor

    @classmethod
    def owner_id(cls, request: Request) -> int:
        """Who a row this request creates (a phone, an account, a watched
        profile) belongs to: the person the request acts as."""
        return cls.user(request).id

    @classmethod
    def scope(cls, request: Request) -> int:
        """Whose rows this request may see and change: the person's own,
        admins included -- nobody reads another person's phones, accounts,
        reels or Drive folders (an admin looks at someone's through "view
        as", which makes that person the request's). Jobs alone reach
        further: an owner also sees their VAs' (`Viewer.actors`).
        "Everyone's" (owner None in the stores) is the background sweeps'."""
        return cls.user(request).id
