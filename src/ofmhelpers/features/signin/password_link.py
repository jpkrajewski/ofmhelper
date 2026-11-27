# Copyright (C) 2026 jpkrajewski

from pydantic import BaseModel, SecretStr, ValidationError

from ofmhelpers.db.users.schema import PasswordForm, User
from ofmhelpers.features.signin.outcome import Outcome, signed_in
from ofmhelpers.features.signin.state import HOME
from ofmhelpers.features.users import credentials

_DEAD_LINK = "This link is used, expired or wrong. Ask an admin for a new one."


class LinkPage(BaseModel):
    owner: User | None
    error: str | None = None
    status_code: int = 200


def page(token: str) -> LinkPage:
    owner = credentials.link_owner(token)
    if owner is None:
        return LinkPage(owner=None, error=_DEAD_LINK, status_code=404)
    return LinkPage(owner=owner)


def set_password(token: str, password: str, confirm: str) -> Outcome | LinkPage:
    try:
        form = PasswordForm(
            password=SecretStr(password),
            confirm=SecretStr(confirm),
        )
        user = credentials.set_password_with_link(
            token,
            form.password.get_secret_value(),
            form.confirm.get_secret_value(),
        )
    except (ValueError, ValidationError) as exc:  # silent: shown on the page
        message = (
            str(exc)
            if isinstance(exc, credentials.PasswordRejectedError)
            else "Fill in both fields"
        )
        return LinkPage(
            owner=credentials.link_owner(token),
            error=message,
            status_code=400,
        )
    if user is None:
        return LinkPage(owner=None, error=_DEAD_LINK, status_code=404)
    return signed_in(user, HOME, "password link")
