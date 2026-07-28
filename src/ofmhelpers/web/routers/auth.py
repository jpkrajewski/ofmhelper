from typing import Annotated

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import RedirectResponse

from ofmhelpers.log import get_logger
from ofmhelpers.web.auth import ROLE_VA, check_password
from ofmhelpers.web.ratelimit import (
    clear_failed_logins,
    client_id,
    login_blocked,
    record_failed_login,
)
from ofmhelpers.web.stores.jobs import log_event
from ofmhelpers.web.templates_config import templates

logger = get_logger(__name__)

router = APIRouter(tags=["auth"])


@router.get("/login")
def login_form(request: Request, next_url: Annotated[str, Query(alias="next")] = "/"):
    if request.session.get("authenticated"):
        return RedirectResponse(url=next_url, status_code=303)
    return templates.TemplateResponse(
        request, "login.html", {"next": next_url, "error": None}
    )


@router.post("/login")
def login_submit(
    request: Request,
    password: Annotated[str, Form()],
    next_url: Annotated[str, Form(alias="next")] = "/",
):
    if login_blocked(request):
        logger.warning("login rate limit hit from %s", client_id(request))
        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "next": next_url,
                "error": "Too many failed attempts. Try again later.",
            },
            status_code=429,
        )

    role = check_password(password)
    if role is None:
        # Every failure is logged and counted: the only signal that someone is
        # guessing, and the only brake on how fast they can.
        record_failed_login(request)
        logger.warning("failed login attempt from %s", client_id(request))
        return templates.TemplateResponse(
            request,
            "login.html",
            {"next": next_url, "error": "Wrong password"},
            status_code=401,
        )

    clear_failed_logins(request)
    request.session["authenticated"] = True
    request.session["role"] = role
    if role == ROLE_VA:
        log_event("login", actor=role)
    return RedirectResponse(url=next_url or "/", status_code=303)


@router.post("/logout")
def logout(request: Request):
    role = request.session.get("role")
    if role == ROLE_VA:
        log_event("logout", actor=role)  # capture before clearing
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)
