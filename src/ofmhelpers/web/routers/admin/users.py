"""
ofmhelpers/web/routers/admin/users.py

/users: who may sign in. An admin adds an email (a Gmail, an Apple ID's
email, any address) with a role; the page then shows a one-time link that
sets their password -- sent to them however you talk to them. They can also
just sign in with Google or Apple under that email. Role, deactivate, a new
link, remove. Nobody can lock themselves out from here: your own row's role,
access and delete are refused. The work: `features/users/service.py`.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import Response

from ofmhelpers.features.users import service
from ofmhelpers.features.users.service import Shown
from ofmhelpers.schemas import Role, User
from ofmhelpers.web.dependencies import AdminOnly, CurrentUser
from ofmhelpers.web.responses import see_other
from ofmhelpers.web.templates_config import render

router = APIRouter(
    prefix="/users",
    tags=["users"],
    dependencies=[AdminOnly],
)

# A page that shows a set-password link: never cached, never sent on.
_PRIVATE = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}

RoleField = Annotated[Role, Form()]


def _page(request: Request, me_id: int, shown: Shown) -> Response:
    view = service.page_view(me_id, shown)
    response = render(
        request, "users.html", **dict(view), status_code=shown.status_code
    )
    response.headers.update(_PRIVATE)
    return response


def _other(user_id: int, me: CurrentUser) -> User:
    """A user that is not the one signed in (no locking yourself out)."""
    return service.other(user_id, me.id)


# The user in the path, when it is not the admin's own row.
OtherUser = Annotated[User, Depends(_other)]


@router.get("")
def users_page(request: Request, me: CurrentUser) -> Response:
    return _page(request, me.id, Shown())


@router.post("")
def add(
    request: Request,
    me: CurrentUser,
    email: Annotated[str, Form()],
    role: RoleField = Role.USER,
    name: Annotated[str, Form()] = "",
) -> Response:
    shown = service.add(email, role, name, str(request.base_url))
    return _page(request, me.id, shown)


@router.post("/{user_id}/link")
def new_link(request: Request, user_id: int, me: CurrentUser) -> Response:
    """A new set-password link (the old one stops working): a first
    password, or a forgotten one."""
    return _page(request, me.id, service.new_link(user_id, str(request.base_url)))


@router.post("/{user_id}/role")
def change_role(user: OtherUser, role: RoleField) -> Response:
    service.set_role(user, role)
    return see_other("/users")


@router.post("/{user_id}/active")
def change_active(user: OtherUser, active: Annotated[bool, Form()]) -> Response:
    service.set_active(user, active)
    return see_other("/users")


@router.post("/{user_id}/delete")
def remove(request: Request, user: OtherUser, me: CurrentUser) -> Response:
    """Their sign-ins and keys go with them. Refused while they own phones,
    accounts or watched profiles: deactivating keeps those."""
    refused = service.remove(user)
    if refused is not None:
        return _page(request, me.id, refused)
    return see_other("/users")
