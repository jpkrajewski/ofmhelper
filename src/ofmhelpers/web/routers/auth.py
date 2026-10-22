"""
Signing in and out (stores/users.py says who may):

- `/login`: a button per configured provider (Google, Apple) and the email
  + password form (rate-limited: failed attempts only).
- `/auth/{provider}/start` -> the provider's consent -> `/auth/{provider}/callback`
  (GET for Google, Apple POSTs its form). The state, nonce, where to go
  after and whether this links an account to the signed-in user (/account)
  travel in a short-lived signed cookie, not the session: Apple's
  cross-site POST does not carry a SameSite=Lax session cookie.
- `/password/{token}`: the one-time link an admin makes on /users, to set a
  password.
- Under OFM_FAKE_EXTERNALS the consent screens are `/auth/fake-consent`.
"""

import secrets
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, Response
from itsdangerous import BadSignature, URLSafeTimedSerializer
from pydantic import BaseModel, ValidationError

from ofmhelpers.cache import enqueue
from ofmhelpers.config import settings
from ofmhelpers.log import get_logger
from ofmhelpers.signin import Identity, Provider, SignInError, configured, providers
from ofmhelpers.signin.fake import FakeAccount, fake_code
from ofmhelpers.signin.owner import owner_code, password_ok
from ofmhelpers.web import drive_folders
from ofmhelpers.web.middleware import AuthMiddleware
from ofmhelpers.web.middleware.ratelimit import (
    clear_failed_logins,
    client_id,
    login_blocked,
    record_failed_login,
)
from ofmhelpers.web.schemas import PasswordForm, User
from ofmhelpers.web.stores import users
from ofmhelpers.web.stores.jobs import log_event
from ofmhelpers.web.templates_config import render

logger = get_logger(__name__)

router = APIRouter(tags=["auth"])

_STATE_COOKIE = "ofm_signin"


# Where a sign-in lands without a page to return to: the app's home ("/" is
# the public landing page).
_HOME = "/home"


class _SignInState(BaseModel):
    """What a consent round trip carries in `_STATE_COOKIE`."""

    provider: str
    state: str
    nonce: str
    next: str = _HOME
    # Set: link the account to this signed-in user instead of signing in.
    link_user_id: int | None = None


def _safe_next(next_url: str | None) -> str:
    """Only a path on this site: never `//evil` or `https://evil`; nothing
    (or the landing page) is the app's home."""
    if (
        not next_url
        or next_url == "/"
        or not next_url.startswith("/")
        or next_url.startswith("//")
    ):
        return _HOME
    return next_url


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.session.session_secret, salt="signin")


def _provider(name: str) -> Provider:
    found = providers().get(name)
    if found is None or not found.configured():
        raise HTTPException(status_code=404, detail="No such sign-in")
    return found


def _redirect_uri(request: Request, provider: Provider) -> str:
    path = f"auth/{provider.name}/callback"
    base = settings.web.base_url
    return f"{base.rstrip('/')}/{path}" if base else str(request.base_url) + path


def _login_page(
    request: Request, next_url: str, error: str | None = None, status_code: int = 200
) -> Response:
    return render(
        request,
        "login.html",
        next=next_url,
        error=error,
        providers=configured(),
        status_code=status_code,
    )


def _signed_in(request: Request, user: User, next_url: str, how: str) -> Response:
    AuthMiddleware.sign_in(request, user)
    log_event("login", actor=str(user.id), params={"email": user.email, "with": how})
    return RedirectResponse(url=_safe_next(next_url), status_code=303)


@router.get("/login")
def login_form(request: Request, next_url: Annotated[str, Query(alias="next")] = _HOME):
    if AuthMiddleware.session_user(request):
        return RedirectResponse(url=_safe_next(next_url), status_code=303)
    return _login_page(request, _safe_next(next_url))


@router.post("/login")
def login_submit(
    request: Request,
    password: Annotated[str, Form()],
    email: Annotated[str, Form()] = users.PASSWORD_ADMIN,
    next_url: Annotated[str, Form(alias="next")] = _HOME,
):
    if login_blocked(request):
        logger.warning("login rate limit hit from %s", client_id(request))
        return _login_page(
            request, next_url, "Too many failed attempts. Try again later.", 429
        )
    user = users.password_sign_in(email, password)
    if user is None:
        # Every failure is logged and counted: the only signal that someone is
        # guessing, and the only brake on how fast they can.
        record_failed_login(request)
        # The email tried (never the password): which account to look at.
        logger.warning(
            "failed login attempt for %r from %s",
            email.strip()[:320],
            client_id(request),
        )
        return _login_page(request, next_url, "Wrong email or password", 401)
    clear_failed_logins(request)
    return _signed_in(request, user, next_url, "password")


@router.post("/logout")
def logout(request: Request):
    user = AuthMiddleware.session_user(request)
    if user is not None:
        log_event("logout", actor=user.actor)  # capture before clearing
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)


# --- Google / Apple ------------------------------------------------------------


@router.get("/auth/{name}/start")
def start(
    request: Request,
    name: str,
    next_url: Annotated[str, Query(alias="next")] = _HOME,
    link: bool = False,
):
    """To the provider's consent. `link`: connect it to the signed-in user
    (/account) -- for Google, asking consent again so a Drive token comes."""
    provider = _provider(name)
    current = AuthMiddleware.session_user(request)
    if link and current is None:
        return RedirectResponse(url="/login", status_code=303)
    carried = _SignInState(
        provider=name,
        state=secrets.token_urlsafe(24),
        nonce=secrets.token_urlsafe(24),
        next=_safe_next(next_url),
        link_user_id=current.id if link and current else None,
    )
    if settings.infra.fake_externals and not provider.local_consent:
        url = f"/auth/fake-consent?provider={name}&state={carried.state}"
    else:
        url = provider.consent_url(
            _redirect_uri(request, provider),
            carried.state,
            carried.nonce,
            connect=link,
        )
    response = RedirectResponse(url=url, status_code=303)
    secure = settings.session.session_https_only
    response.set_cookie(
        _STATE_COOKIE,
        _serializer().dumps(carried.model_dump()),
        max_age=settings.signin.state_ttl_s,
        httponly=True,
        secure=secure,
        # Apple comes back with a cross-site POST: only SameSite=None rides
        # along, and browsers take that only on a Secure cookie.
        samesite="none" if secure else "lax",
        path="/auth/",
    )
    return response


def _carried(request: Request, name: str, state: str) -> _SignInState:
    raw = request.cookies.get(_STATE_COOKIE, "")
    try:
        carried = _SignInState.model_validate(
            _serializer().loads(raw, max_age=settings.signin.state_ttl_s)
        )
    except (BadSignature, ValidationError) as exc:
        msg = "This sign-in took too long or was not started here"
        raise SignInError(msg) from exc
    if carried.provider != name or not secrets.compare_digest(carried.state, state):
        msg = "This sign-in was not started here: try again"
        raise SignInError(msg)
    return carried


def _identity(
    request: Request, provider: Provider, code: str, state: str, error: str
) -> tuple[_SignInState, Identity]:
    """Who the callback says signed in. Raises `SignInError`."""
    if error:
        msg = f"{provider.label} sign-in was cancelled"
        raise SignInError(msg)
    carried = _carried(request, provider.name, state)
    reply = provider.exchange(code, _redirect_uri(request, provider))
    return carried, provider.identity(reply, carried.nonce)


def _sync_new_drive(user_id: int, refresh_token: str | None) -> None:
    """A Drive token just arrived: build that person's root and account
    folders now (on the worker) instead of at the next sweep, so /clone's
    upload and /drive work straight after connecting."""
    if refresh_token:
        enqueue(drive_folders.sync, user_id)


def _finish(request: Request, name: str, code: str, state: str, error: str) -> Response:
    provider = _provider(name)
    try:
        carried, identity = _identity(request, provider, code, state, error)
    except SignInError as exc:
        logger.warning("sign-in with %s failed: %s", name, exc)
        # Someone already signed in was connecting an account (Drive): back to
        # /account to try again, not a login page that invites a plain
        # sign-in, which never asks for Drive (prod, 2026-10-22).
        response: Response
        if AuthMiddleware.session_user(request) is not None:
            response = RedirectResponse(url=f"/account?{name}=failed", status_code=303)
        else:
            response = _login_page(request, _HOME, str(exc), 400)
        response.delete_cookie(_STATE_COOKIE, path="/auth/")
        return response
    if carried.link_user_id is not None:
        linked = users.link_identity(carried.link_user_id, identity)
        note = "linked" if linked else "taken"
        if linked:
            _sync_new_drive(carried.link_user_id, identity.refresh_token)
        response = RedirectResponse(url=f"/account?{name}={note}", status_code=303)
    else:
        user = users.sign_in_identity(identity)
        if user is not None:
            _sync_new_drive(user.id, identity.refresh_token)
        if user is None:
            logger.warning("sign-in with %s refused for %s", name, identity.email)
            response = _login_page(
                request,
                carried.next,
                f"{identity.email or 'This account'} has no access. Ask an admin"
                " to add it on Users.",
                403,
            )
        else:
            response = _signed_in(request, user, carried.next, name)
    response.delete_cookie(_STATE_COOKIE, path="/auth/")
    return response


@router.get("/auth/{name}/callback")
def callback_get(
    request: Request, name: str, code: str = "", state: str = "", error: str = ""
):
    return _finish(request, name, code, state, error)


@router.post("/auth/{name}/callback")
def callback_post(
    request: Request,
    name: str,
    code: Annotated[str, Form()] = "",
    state: Annotated[str, Form()] = "",
    error: Annotated[str, Form()] = "",
):
    """Apple's form_post return."""
    return _finish(request, name, code, state, error)


@router.get("/auth/owner/consent")
def owner_consent(request: Request, state: str = ""):
    """The app owner's consent screen: the admin password."""
    return render(request, "owner_consent.html", state=state, error=None)


@router.post("/auth/owner/consent")
def owner_consent_submit(
    request: Request,
    state: Annotated[str, Form()],
    password: Annotated[str, Form()],
):
    """The right admin password goes on to the callback with a code for
    this one sign-in; a wrong one is counted like a failed /login."""

    def again(error: str, status_code: int) -> Response:
        return render(
            request,
            "owner_consent.html",
            state=state,
            error=error,
            status_code=status_code,
        )

    if login_blocked(request):
        logger.warning("login rate limit hit from %s", client_id(request))
        return again("Too many failed attempts. Try again later.", 429)
    try:
        carried = _carried(request, "owner", state)
    except SignInError as exc:
        return again(str(exc), 400)
    if not password_ok(password):
        record_failed_login(request)
        logger.warning("failed app owner sign-in from %s", client_id(request))
        return again("Wrong admin password", 401)
    clear_failed_logins(request)
    query = urlencode({"code": owner_code(carried.nonce), "state": state})
    return RedirectResponse(url=f"/auth/owner/callback?{query}", status_code=303)


@router.get("/auth/fake-consent")
def fake_consent(request: Request, provider: str, state: str):
    """The providers' consent screens under OFM_FAKE_EXTERNALS (e2e)."""
    if not settings.infra.fake_externals:
        raise HTTPException(status_code=404)
    return render(request, "fake_consent.html", provider=provider, state=state)


@router.post("/auth/fake-consent")
def fake_consent_submit(
    request: Request,
    provider: Annotated[str, Form()],
    state: Annotated[str, Form()],
    email: Annotated[str, Form()],
):
    if not settings.infra.fake_externals:
        raise HTTPException(status_code=404)
    try:
        carried = _carried(request, provider, state)
    except SignInError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # Like Google: a Drive (refresh) token only when connecting, never on a
    # plain sign-in.
    code = fake_code(
        FakeAccount(
            email=email.strip().lower(),
            nonce=carried.nonce,
            refresh_token=carried.link_user_id is not None,
        )
    )
    return RedirectResponse(
        url=f"/auth/{provider}/callback?code={code}&state={state}", status_code=303
    )


# --- set-password links --------------------------------------------------------


@router.get("/password/{token}")
def password_form(request: Request, token: str):
    user = users.link_owner(token)
    return render(
        request,
        "password_set.html",
        user=user,
        error=None
        if user
        else "This link is used, expired or wrong. Ask an admin for a new one.",
        min_length=settings.signin.password_min_length,
        status_code=200 if user else 404,
    )


@router.post("/password/{token}")
def password_submit(
    request: Request,
    token: str,
    password: Annotated[str, Form()],
    confirm: Annotated[str, Form()],
):
    try:
        form = PasswordForm(password=password, confirm=confirm)
        user = users.set_password_with_link(token, form.password, form.confirm)
    except (ValueError, ValidationError) as exc:
        message = (
            str(exc)
            if isinstance(exc, users.PasswordRejectedError)
            else "Fill in both fields"
        )
        return render(
            request,
            "password_set.html",
            user=users.link_owner(token),
            error=message,
            min_length=settings.signin.password_min_length,
            status_code=400,
        )
    if user is None:
        return render(
            request,
            "password_set.html",
            user=None,
            error="This link is used, expired or wrong. Ask an admin for a new one.",
            min_length=settings.signin.password_min_length,
            status_code=404,
        )
    return _signed_in(request, user, _HOME, "password link")
