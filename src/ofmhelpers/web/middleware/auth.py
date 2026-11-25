# Copyright (C) 2026 jpkrajewski

import logging
import secrets
import time
from urllib.parse import quote, urlsplit

from fastapi import HTTPException
from opentelemetry import trace
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import (
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)
lazy from starlette.requests import HTTPConnection, Request

from ofmhelpers.acting import Acting, Via, acting
from ofmhelpers.config import settings
from ofmhelpers.db.users.schema import Role, SessionUser, User
from ofmhelpers.features.signin import state as signin_state
from ofmhelpers.features.users import presence
from ofmhelpers.features.users.viewing import (
    Seated,
    signed_in_user,
    viewer_for,
    viewing_ended,
    viewing_started,
)
from ofmhelpers.monitoring.sentry import name_user
from ofmhelpers.web.access import Action, Area, opens
lazy from ofmhelpers.db.users.schema import Viewer

logger = logging.getLogger(__name__)

_KEYS = ("user_id", "email", "role")
_STAMP = "stamp"
_SEEN = "seen"
_SINCE = "since"
_SID = "sid"
SESSION_PATH = "/session"
_VIEWING = "viewing"
_READS = frozenset({"GET", "HEAD", "OPTIONS"})
STOP_VIEWING_PATH = "/viewing/delete"
_WAYS_OUT = frozenset({STOP_VIEWING_PATH, "/logout", SESSION_PATH})


def _request(request: Request) -> Acting:
    context = trace.get_current_span().get_span_context()
    trace_id = format(context.trace_id, "032x") if context.is_valid else ""
    return Acting(
        via=Via.REQUEST,
        ref=f"{request.method} {request.url.path}",
        trace_id=trace_id,
    )


class AuthMiddleware(BaseHTTPMiddleware):
    HOME = signin_state.HOME

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        if self.is_public(request.url.path):
            with acting(_request(request)):
                return await call_next(request)
        current = self.session_user(request)
        seated = (
            await run_in_threadpool(signed_in_user, current.id) if current else None
        )
        reason = self._refused(request, current, seated)
        if reason is not None or current is None or seated is None:
            if reason != "no session" or request.cookies.get("ofm_session"):
                logger.warning(
                    "auth: %s refused (%s), user %s",
                    request.url.path,
                    reason,
                    current.id if current else "-",
                )
            request.session.clear()
            return self._to_login(request)
        user = seated.user
        if user.role != current.role or user.email != current.email:
            self.remember(request, user)
        if self._acted(request):
            now = time.time()
            request.session[_SEEN] = int(now)
            await run_in_threadpool(presence.seen, user, now)
        viewer = await run_in_threadpool(
            viewer_for,
            user,
            request.session.get(_VIEWING),
        )
        if viewer.by is None:
            request.session.pop(_VIEWING, None)
        request.state.viewer = viewer
        trace.get_current_span().set_attributes(
            {
                "enduser.id": str(user.id),
                "enduser.email": user.email,
                "enduser.role": str(user.role),
                **({"viewing.id": str(viewer.user.id)} if viewer.by else {}),
            },
        )
        name_user(user.id)
        if (
            viewer.read_only
            and request.method not in _READS
            and request.url.path not in _WAYS_OUT
        ):
            return self._look_only(request, viewer)
        who = _request(request).model_copy(
            update={
                "actor_id": (viewer.by or viewer.user).id,
                "viewing_id": viewer.user.id if viewer.by else None,
            },
        )
        with acting(who):
            return await call_next(request)

    @staticmethod
    def _acted(request: Request) -> bool:
        if request.method not in _READS:
            return True
        return request.headers.get("sec-fetch-mode") == "navigate"

    def _look_only(self, request: Request, viewer: Viewer) -> Response:
        detail = f"You are viewing as {viewer.label}. Go back to your view to do this."
        if viewer.managing:
            detail = "A manager looks only: nothing can be changed here."
        if self.is_fetch(request):
            return JSONResponse({"detail": detail}, status_code=403)
        return PlainTextResponse(detail, status_code=403)

    @staticmethod
    def _refused(  # noqa: PLR0911
        request: HTTPConnection,
        current: SessionUser | None,
        seated: Seated | None,
    ) -> str | None:
        if current is None:
            return "no session"
        if seated is None:
            return "user gone"
        standing = seated.standing
        if not standing.active:
            return "deactivated"
        if standing.role == Role.VA and standing.employer_active is False:
            return "employer deactivated"
        if request.session.get(_STAMP, "") != standing.credential_stamp:
            return "password changed"
        ended = standing.sessions_ended_at
        if ended is not None and request.session.get(_SINCE, 0) <= ended:
            return "logged out"
        last = request.session.get(_SEEN) or request.session.get(_SINCE)
        if last and time.time() - last > presence.idle_limit_s(standing.role):
            return "idle"
        return None

    def _to_login(self, request: Request) -> Response:
        if self.is_fetch(request):
            return JSONResponse(
                {
                    "detail": "Session expired",
                    "login_url": self._login_url(self._calling_page(request)),
                },
                status_code=401,
            )
        if "dl" in request.query_params:
            return PlainTextResponse(
                "Signed out: sign in again, then download",
                status_code=401,
            )
        next_url = request.url.path
        if request.url.query:
            next_url += f"?{request.url.query}"
        return RedirectResponse(url=self._login_url(next_url), status_code=303)

    @staticmethod
    def _login_url(next_url: str | None) -> str:
        return f"/login?next={quote(next_url, safe='/')}" if next_url else "/login"

    @staticmethod
    def _calling_page(request: Request) -> str | None:
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
        mode = request.headers.get("sec-fetch-mode")
        if mode:
            return mode != "navigate"
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return True
        accept = request.headers.get("accept", "")
        return "application/json" in accept and "text/html" not in accept

    @staticmethod
    def remember(request: Request, user: User) -> None:
        request.session.update(
            {
                "user_id": user.id,
                "email": user.email,
                "role": str(user.role),
                _STAMP: user.credential_stamp,
            },
        )

    @classmethod
    def sign_in(cls, request: Request, user: User) -> None:
        request.session.clear()
        cls.remember(request, user)
        request.session[_SINCE] = time.time()

    @staticmethod
    def session_id(request: HTTPConnection) -> str:
        found = request.session.get(_SID)
        if not found:
            found = secrets.token_urlsafe(16)
            request.session[_SID] = found
        return found

    @staticmethod
    def signed_in_at(request: Request) -> float | None:
        return request.session.get(_SINCE)

    @staticmethod
    def session_user(request: HTTPConnection) -> SessionUser | None:
        session = request.session
        if not all(k in session for k in _KEYS):
            return None
        return SessionUser(
            id=session["user_id"],
            email=session["email"],
            role=session["role"],
        )

    @classmethod
    def valid_user(cls, request: HTTPConnection) -> User | None:
        current = cls.session_user(request)
        seated = signed_in_user(current.id) if current else None
        if cls._refused(request, current, seated) is not None or seated is None:
            return None
        return seated.user

    @classmethod
    def socket_viewer(cls, connection: HTTPConnection) -> Viewer | None:
        user = cls.valid_user(connection)
        if user is None:
            return None
        return viewer_for(user, connection.session.get(_VIEWING))

    @staticmethod
    def view_as(request: Request, user: User) -> None:
        request.session[_VIEWING] = user.id
        admin = AuthMiddleware.session_user(request)
        viewing_started(admin.id if admin else None, user)

    @staticmethod
    def stop_viewing(request: Request) -> None:
        viewed = request.session.pop(_VIEWING, None)
        admin = AuthMiddleware.session_user(request)
        if viewed is not None:
            viewing_ended(admin.id if admin else None, viewed)

    @staticmethod
    def viewer(request: Request) -> Viewer:
        if "viewer" not in request.state:
            raise HTTPException(status_code=401, detail="Not signed in")
        found: Viewer = request.state["viewer"]
        return found

    @classmethod
    def user(cls, request: Request) -> SessionUser:
        return cls.viewer(request).user

    @classmethod
    def require(cls, request: Request, what: Area | Action) -> None:
        if opens(cls.viewer(request), what):
            return
        if request.method == "GET" and cls.is_page_load(request):
            raise HTTPException(status_code=303, headers={"Location": cls.HOME})
        raise HTTPException(status_code=403, detail="Not available to you")

    @staticmethod
    def is_page_load(request: Request) -> bool:
        return request.headers.get("sec-fetch-mode") == "navigate"

    @classmethod
    def actor(cls, request: Request) -> str:
        return cls.user(request).actor

    @classmethod
    def owner_id(cls, request: Request) -> int:
        return cls.user(request).id

    @classmethod
    def scope(cls, request: Request) -> int:
        return cls.user(request).id
