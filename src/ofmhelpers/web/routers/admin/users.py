"""
ofmhelpers/web/routers/admin/users.py

/users: who may sign in. An admin adds an email (a Gmail, an Apple ID's
email, any address) with a role; the page then shows a one-time link that
sets their password -- sent to them however you talk to them. They can also
just sign in with Google or Apple under that email. Role, deactivate, a new
link, remove. Nobody can lock themselves out from here: your own row's role,
access and delete are refused.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from pydantic import ValidationError

from ofmhelpers.config import settings
from ofmhelpers.signin import configured
from ofmhelpers.web.db.repositories import OwnsDataError
from ofmhelpers.web.middleware import AuthMiddleware
from ofmhelpers.web.schemas import Role, User, UserForm
from ofmhelpers.web.stores import users
from ofmhelpers.web.templates_config import render

router = APIRouter(
    prefix="/users",
    tags=["users"],
    dependencies=[Depends(AuthMiddleware.require_admin)],
)

# A page that shows a set-password link: never cached, never sent on.
_PRIVATE = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}


def _link_url(request: Request, token: str) -> str:
    base = settings.web.base_url or str(request.base_url)
    return f"{base.rstrip('/')}/password/{token}"


def _page(
    request: Request,
    *,
    error: str | None = None,
    link_for: User | None = None,
    link: str | None = None,
    status_code: int = 200,
) -> Response:
    response = render(
        request,
        "users.html",
        users=users.list_users(),
        me=AuthMiddleware.user(request).id,
        roles=list(Role),
        providers=configured(),
        env_admins=settings.signin.admin_emails,
        link_for=link_for,
        link=link,
        link_days=settings.signin.password_link_ttl_s // 86400,
        error=error,
        status_code=status_code,
    )
    response.headers.update(_PRIVATE)
    return response


def _other(request: Request, user_id: int) -> User:
    """A user that is not the one signed in (no locking yourself out)."""
    if user_id == AuthMiddleware.user(request).id:
        raise HTTPException(status_code=400, detail="Not on your own account")
    user = users.get_user(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="No such user")
    return user


@router.get("")
def users_page(request: Request):
    return _page(request)


@router.post("")
def add(
    request: Request,
    email: Annotated[str, Form()],
    role: Annotated[Role, Form()] = Role.USER,
    name: Annotated[str, Form()] = "",
):
    try:
        form = UserForm(email=email, role=role, name=name)
    except ValidationError:
        return _page(
            request, error=f"{email!r} is not an email address", status_code=400
        )
    user = users.add_user(form)
    if user is None:
        return _page(request, error=f"{form.email} is already a user", status_code=409)
    return _page(
        request, link_for=user, link=_link_url(request, users.password_link(user.id))
    )


@router.post("/{user_id}/link")
def new_link(request: Request, user_id: int):
    """A new set-password link (the old one stops working): a first
    password, or a forgotten one."""
    user = users.get_user(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="No such user")
    return _page(
        request, link_for=user, link=_link_url(request, users.password_link(user.id))
    )


@router.post("/{user_id}/role")
def change_role(request: Request, user_id: int, role: Annotated[Role, Form()]):
    _other(request, user_id)
    users.set_role(user_id, role)
    return RedirectResponse(url="/users", status_code=303)


@router.post("/{user_id}/active")
def change_active(request: Request, user_id: int, active: Annotated[bool, Form()]):
    _other(request, user_id)
    users.set_active(user_id, active=active)
    return RedirectResponse(url="/users", status_code=303)


@router.post("/{user_id}/delete")
def remove(request: Request, user_id: int):
    """Their sign-ins and keys go with them. Refused while they own phones,
    accounts or watched profiles: deactivating keeps those."""
    user = _other(request, user_id)
    try:
        users.delete_user(user_id)
    except OwnsDataError:
        return _page(
            request,
            error=f"{user.email} still owns phones, accounts or watched profiles:"
            " deactivate them instead (nothing of theirs is lost).",
            status_code=409,
        )
    return RedirectResponse(url="/users", status_code=303)
