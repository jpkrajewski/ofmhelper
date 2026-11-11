"""Email + password: who a typed password signs in, the one-time
set-password links, and setting or changing a password. The hashes are
`integrations/signin/passwords.py`'s; `db/stores/users.py` keeps them."""

import hmac
import time

from ofmhelpers.config import settings
from ofmhelpers.db.stores import users
from ofmhelpers.integrations.signin.passwords import (
    hash_password,
    link_hash,
    new_link_token,
    verify_password,
)
from ofmhelpers.schemas.users import Role, normalized_email
lazy from ofmhelpers.schemas import User


class PasswordRejectedError(ValueError):
    """A new password not accepted; the message says why."""


def _is_admin_password(password: str) -> bool:
    admin_password = settings.web.app_password_admin
    return bool(admin_password) and hmac.compare_digest(
        password.encode(), (admin_password or "").encode()
    )


def password_sign_in(email: str, password: str) -> User | None:
    """The user, when the password is theirs; None otherwise -- after the
    same work whether or not the email exists. `APP_PASSWORD_ADMIN` also
    opens the password account ("admin") and every `OFM_ADMIN_EMAILS`
    address (as that admin), so an admin gets in with their own email
    before any Google/Apple sign-in is set up."""
    email = normalized_email(email)
    if email == users.PASSWORD_ADMIN or users.is_env_admin(email):
        # Every admin address is the agency admin. Read without side
        # effects: only the admin password makes or restores the account
        # (break-glass); its own password opens it only while it is active.
        account = users.password_account()
        stored = users.password_hash(account.id) if account else ""
        own = verify_password(password, stored)
        if _is_admin_password(password):
            return users.admit(users.env_admin(email, "") or users.password_admin())
        return users.admit(account) if own else None
    user = users.by_email(email)
    stored = users.password_hash(user.id) if user else ""
    if verify_password(password, stored) and user is not None:
        # A VA signs in with "Login as Virtual Assistant" (`va_sign_in`),
        # never here.
        return None if user.role == Role.VA else users.admit(user)
    return None


def _vas_of(owner_id: int) -> list[User]:
    """`owner_id`'s VAs that have a password."""
    return [va for va in users.vas_of(owner_id) if va.has_password]


def va_sign_in(employer_email: str, password: str) -> User | None:
    """ "Login as Virtual Assistant": the active VA of the employer with that
    email whose password this is. A VA is named by their employer's email
    plus their own password, so two employers' VAs never collide, and no
    VA's password can be anyone else's sign-in. Every VA's hash of that
    employer is tried, found or not; an unknown employer costs one check
    too."""
    employer = users.by_email(normalized_email(employer_email))
    vas = _vas_of(employer.id) if employer is not None else []
    if not vas:
        verify_password(password, "")
        return None
    found = [va for va in vas if verify_password(password, users.password_hash(va.id))]
    return users.admit(found[0]) if found else None


def _free_for(owner_id: int, user_id: int | None, password: str) -> str:
    """`password` checked as a VA's of `owner_id`: long enough, and no other
    VA of that employer has it. Raises `PasswordRejectedError`."""
    checked = _checked(password, password)
    taken = any(
        va.id != user_id and verify_password(checked, users.password_hash(va.id))
        for va in _vas_of(owner_id)
    )
    if taken:
        msg = "Another VA of this employer has that password: pick another"
        raise PasswordRejectedError(msg)
    return checked


def check_va_password(owner_id: int, password: str) -> None:
    """`set_va_password`'s checks before the VA exists. Raises
    `PasswordRejectedError`."""
    _free_for(owner_id, None, password)


def set_va_password(user_id: int, owner_id: int, password: str) -> None:
    """The password an employer's VA signs in with, set by an admin on
    /users. Raises `PasswordRejectedError`."""
    users.set_password_hash(
        user_id, hash_password(_free_for(owner_id, user_id, password))
    )


def _checked(password: str, confirm: str) -> str:
    minimum = settings.signin.password_min_length
    if len(password) < minimum:
        msg = f"Use at least {minimum} characters"
        raise PasswordRejectedError(msg)
    if password != confirm:
        msg = "The two passwords differ"
        raise PasswordRejectedError(msg)
    return password


def password_link(user_id: int) -> str:
    """A new one-time set-password token for the user (the previous one
    stops working); valid `password_link_ttl_s`."""
    link = new_link_token()
    expires = time.time() + settings.signin.password_link_ttl_s
    users.add_password_link(user_id, link.token_hash, expires)
    return link.token


def link_owner(token: str) -> User | None:
    """The active user an unexpired link is for."""
    user_id = users.link_user(link_hash(token), time.time())
    user = users.get_user(user_id) if user_id is not None else None
    return user if user is not None and user.active else None


def set_password_with_link(token: str, password: str, confirm: str) -> User | None:
    """Sets the password and signs in; None for a bad or used link. Raises
    `PasswordRejectedError` for a password not accepted."""
    user = link_owner(token)
    if user is None:
        return None
    users.set_password_hash(user.id, hash_password(_checked(password, confirm)))
    return users.admit(user)


def change_password(user_id: int, current: str, password: str, confirm: str) -> None:
    """/account. `current` is asked only when there is one. Raises
    `PasswordRejectedError`."""
    stored = users.password_hash(user_id)
    if stored and not verify_password(current, stored):
        msg = "The current password is wrong"
        raise PasswordRejectedError(msg)
    users.set_password_hash(user_id, hash_password(_checked(password, confirm)))
