from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse

from ofmhelpers.web.templates_config import templates
from ofmhelpers.web.auth import check_password
from ofmhelpers.web.jobs import log_event

router = APIRouter(tags=["auth"])


@router.get("/login")
def login_form(request: Request, next: str = "/"):
    if request.session.get("authenticated"):
        return RedirectResponse(url=next, status_code=303)
    return templates.TemplateResponse(
        request, "login.html", {"next": next, "error": None}
    )


@router.post("/login")
def login_submit(
    request: Request,
    password: str = Form(...),
    next: str = Form("/"),
):
    role = check_password(password)
    if role is None:
        return templates.TemplateResponse(
            request,
            "login.html",
            {"next": next, "error": "Wrong password"},
            status_code=401,
        )

    request.session["authenticated"] = True
    request.session["role"] = role
    if role == "va":
        log_event("login", actor=role)
    return RedirectResponse(url=next or "/", status_code=303)


@router.post("/logout")
def logout(request: Request):
    role = request.session.get("role")
    if role == "va":
        log_event("logout", actor=role)  # capture before clearing
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)
