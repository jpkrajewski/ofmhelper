"""`/password/{token}`: the one-time link an admin makes on /users."""

from pydantic import BaseModel, ValidationError

from ofmhelpers.db.stores import users
from ofmhelpers.features.signin.outcome import Outcome, signed_in
from ofmhelpers.features.signin.state import HOME
from ofmhelpers.schemas import PasswordForm, User

_DEAD_LINK = "This link is used, expired or wrong. Ask an admin for a new one."


class LinkPage(BaseModel):
    """The set-password page: whose link it is, and what went wrong."""

    owner: User | None
    error: str | None = None
    status_code: int = 200


def page(token: str) -> LinkPage:
    owner = users.link_owner(token)
    if owner is None:
        return LinkPage(owner=None, error=_DEAD_LINK, status_code=404)
    return LinkPage(owner=owner)


def set_password(token: str, password: str, confirm: str) -> Outcome | LinkPage:
    try:
        form = PasswordForm(password=password, confirm=confirm)
        user = users.set_password_with_link(token, form.password, form.confirm)
    except (ValueError, ValidationError) as exc:
        message = (
            str(exc)
            if isinstance(exc, users.PasswordRejectedError)
            else "Fill in both fields"
        )
        return LinkPage(owner=users.link_owner(token), error=message, status_code=400)
    if user is None:
        return LinkPage(owner=None, error=_DEAD_LINK, status_code=404)
    return signed_in(user, HOME, "password link")
