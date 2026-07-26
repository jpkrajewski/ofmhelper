from typing import Annotated

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import RedirectResponse

from ofmhelpers.web.auth import ROLE_VA, check_password
from ofmhelpers.web.jobs import log_event
from ofmhelpers.web.templates_config import templates

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
    role = check_password(password)
    if role is None:
        return templates.TemplateResponse(
            request,
            "login.html",
            {"next": next_url, "error": "Wrong password"},
            status_code=401,
        )

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
