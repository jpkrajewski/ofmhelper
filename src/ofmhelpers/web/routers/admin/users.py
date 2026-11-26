# Copyright (C) 2026 jpkrajewski

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import Response

from ofmhelpers.db.users.schema import Role, User
from ofmhelpers.features.users import service, viewing
from ofmhelpers.features.users.service import Shown
from ofmhelpers.web.access import Area
from ofmhelpers.web.dependencies import Access, CurrentUser
from ofmhelpers.web.middleware import AuthMiddleware
from ofmhelpers.web.responses import see_other
from ofmhelpers.web.templates_config import render

router = APIRouter(
    prefix="/users",
    tags=["users"],
    dependencies=[Access(Area.USERS)],
)

_PRIVATE = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}

RoleField = Annotated[Role, Form()]


def _page(request: Request, me_id: int, shown: Shown) -> Response:
    view = service.page_view(me_id, shown)
    response = render(request, "users.html", dict(view), status_code=shown.status_code)
    response.headers.update(_PRIVATE)
    return response


def _other(user_id: int, me: CurrentUser) -> User:
    return service.other(user_id, me.id)


OtherUser = Annotated[User, Depends(_other)]


@router.get("")
def users_page(request: Request, me: CurrentUser) -> Response:
    return _page(request, me.id, Shown())


@router.post("")
def add(  # noqa: PLR0913, PLR0917
    request: Request,
    me: CurrentUser,
    email: Annotated[str, Form()],
    role: RoleField = Role.USER,
    name: Annotated[str, Form()] = "",
    owner_id: Annotated[int | None, Form()] = None,
    password: Annotated[str, Form()] = "",
) -> Response:
    shown = service.add(email, role, name, str(request.base_url), owner_id, password)
    return _page(request, me.id, shown)


@router.post("/{user_id}/link")
def new_link(request: Request, user_id: int, me: CurrentUser) -> Response:
    return _page(request, me.id, service.new_link(user_id, str(request.base_url)))


@router.post("/{user_id}/password")
def set_password(
    request: Request,
    user: OtherUser,
    me: CurrentUser,
    password: Annotated[str, Form()],
) -> Response:
    return _page(request, me.id, service.set_va_password(user, password))


@router.post("/{user_id}/owner")
def change_owner(
    user: OtherUser,
    owner_id: Annotated[int | None, Form()] = None,
) -> Response:
    service.set_owner(user, owner_id)
    return see_other("/users")


@router.post("/{user_id}/viewing")
def view_as(request: Request, user_id: int, me: CurrentUser) -> Response:
    AuthMiddleware.view_as(request, viewing.viewable(user_id, me.id))
    return see_other("/home")


@router.post("/{user_id}/active")
def change_active(user: OtherUser, active: Annotated[bool, Form()]) -> Response:
    service.set_active(user, active)
    return see_other("/users")


@router.post("/{user_id}/delete")
def remove(request: Request, user: OtherUser, me: CurrentUser) -> Response:
    refused = service.remove(user)
    if refused is not None:
        return _page(request, me.id, refused)
    return see_other("/users")
