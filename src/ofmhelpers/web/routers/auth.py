"""
Signing in and out: routes only. `features/signin/` does the work,
stores/users.py says who may.

- `/login`: a button per configured provider and the email + password form.
- `/auth/{provider}/start` -> the provider's consent -> `/auth/{provider}/callback`
  (GET for Google, Apple POSTs its form).
- `/password/{token}`: the one-time link an admin makes on /users.
- Under OFM_FAKE_EXTERNALS the consent screens are `/auth/fake-consent`.
"""

from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, Response

from ofmhelpers.config import settings
from ofmhelpers.db.stores import users
from ofmhelpers.db.stores.jobs import log_event
from ofmhelpers.features.signin import consent, flow, password_link, passwords
from ofmhelpers.features.signin.outcome import Outcome
from ofmhelpers.features.signin.state import (
    HOME,
    STATE_COOKIE,
    carry_state,
    drop_state,
    safe_next,
)
from ofmhelpers.integrations.signin import Provider, SignInError, configured, providers
from ofmhelpers.web.middleware import AuthMiddleware
from ofmhelpers.web.templates_config import render

router = APIRouter(tags=["auth"])


def _provider(name: str) -> Provider:
    found = providers().get(name)
    if found is None or not found.configured():
        raise HTTPException(status_code=404, detail="No such sign-in")
    return found


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


def _respond(request: Request, outcome: Outcome) -> Response:
    """On to `redirect`, signed in if a user is named; else the login page."""
    if outcome.redirect is None:
        return _login_page(
            request, outcome.next_url, outcome.error, outcome.status_code
        )
    if outcome.user is not None:
        AuthMiddleware.sign_in(request, outcome.user)
    return RedirectResponse(url=outcome.redirect, status_code=303)


@router.get("/login")
def login_form(request: Request, next_url: Annotated[str, Query(alias="next")] = HOME):
    if AuthMiddleware.session_user(request):
        return RedirectResponse(url=safe_next(next_url), status_code=303)
    return _login_page(request, safe_next(next_url))


@router.post("/login")
def login_submit(
    request: Request,
    password: Annotated[str, Form()],
    email: Annotated[str, Form()] = users.PASSWORD_ADMIN,
    next_url: Annotated[str, Form(alias="next")] = HOME,
):
    return _respond(request, passwords.login(request, email, password, next_url))


@router.post("/logout")
def logout(request: Request):
    user = AuthMiddleware.session_user(request)
    if user is not None:
        log_event("logout", actor=user.actor)  # capture before clearing
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)


@router.get("/auth/{name}/start")
def start(
    request: Request,
    name: str,
    next_url: Annotated[str, Query(alias="next")] = HOME,
    link: bool = False,
):
    """To the provider's consent. `link`: connect it to the signed-in user
    (/account) -- for Google, asking consent again so a Drive token comes."""
    provider = _provider(name)
    current = AuthMiddleware.session_user(request)
    if link and current is None:
        return RedirectResponse(url="/login", status_code=303)
    link_user_id = current.id if link and current else None
    begun = consent.begin(provider, str(request.base_url), next_url, link_user_id)
    response = RedirectResponse(url=begun.url, status_code=303)
    carry_state(response, begun.cookie)
    return response


def _finish(request: Request, name: str, code: str, state: str, error: str) -> Response:
    provider = _provider(name)
    back = flow.Callback(
        code=code,
        state=state,
        error=error,
        cookie=request.cookies.get(STATE_COOKIE, ""),
        site=str(request.base_url),
        signed_in=AuthMiddleware.session_user(request) is not None,
    )
    response = _respond(request, flow.finish(provider, back))
    drop_state(response)
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
    outcome = passwords.owner_consent(request, state, password)
    if outcome.redirect is None:
        return render(
            request,
            "owner_consent.html",
            state=state,
            error=outcome.error,
            status_code=outcome.status_code,
        )
    return RedirectResponse(url=outcome.redirect, status_code=303)


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
    cookie = request.cookies.get(STATE_COOKIE, "")
    try:
        url = consent.fake_callback(cookie, provider, state, email)
    except SignInError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url=url, status_code=303)


def _password_page(request: Request, page: password_link.LinkPage) -> Response:
    return render(
        request,
        "password_set.html",
        user=page.owner,
        error=page.error,
        min_length=settings.signin.password_min_length,
        status_code=page.status_code,
    )


@router.get("/password/{token}")
def password_form(request: Request, token: str):
    return _password_page(request, password_link.page(token))


@router.post("/password/{token}")
def password_submit(
    request: Request,
    token: str,
    password: Annotated[str, Form()],
    confirm: Annotated[str, Form()],
):
    done = password_link.set_password(token, password, confirm)
    if isinstance(done, password_link.LinkPage):
        return _password_page(request, done)
    return _respond(request, done)
