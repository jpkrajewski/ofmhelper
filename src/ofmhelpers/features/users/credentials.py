import hmac
import time

from ofmhelpers.config import settings
from ofmhelpers.db.users import store as users
from ofmhelpers.db.users.schema import Role, normalized_email
from ofmhelpers.integrations.signin.passwords import (
    hash_password,
    link_hash,
    new_link_token,
    verify_password,
)
lazy from ofmhelpers.db.users.schema import User


class PasswordRejectedError(ValueError): ...


class WrongPasswordError(PasswordRejectedError): ...


def _is_admin_password(password: str) -> bool:
    admin_password = settings.web.app_password_admin
    return bool(admin_password) and hmac.compare_digest(
        password.encode(), (admin_password or "").encode()
    )


def password_sign_in(email: str, password: str) -> User | None:
    email = normalized_email(email)
    if email == users.PASSWORD_ADMIN or users.is_env_admin(email):
        account = users.password_account()
        stored = users.password_hash(account.id) if account else ""
        own = verify_password(password, stored)
        if _is_admin_password(password):
            restored = users.password_admin()
            return users.admit(users.env_admin(email, "") or restored)
        return users.admit(account) if own else None
    user = users.by_email(email)
    stored = users.password_hash(user.id) if user else ""
    if verify_password(password, stored) and user is not None:
        return None if user.role == Role.VA else users.admit(user)
    return None


def _vas_of(owner_id: int) -> list[User]:
    return [va for va in users.vas_of(owner_id) if va.has_password]


def va_sign_in(employer_email: str, password: str) -> User | None:
    employer = users.by_email(normalized_email(employer_email))
    working = employer is not None and employer.active
    vas = _vas_of(employer.id) if employer is not None and working else []
    if not vas:
        verify_password(password, "")
        return None
    found = [va for va in vas if verify_password(password, users.password_hash(va.id))]
    return users.admit(found[0]) if found else None


def _free_for(owner_id: int, user_id: int | None, password: str) -> str:
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
    _free_for(owner_id, None, password)


def set_va_password(user_id: int, owner_id: int, password: str) -> None:
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
    link = new_link_token()
    expires = time.time() + settings.signin.password_link_ttl_s
    users.add_password_link(user_id, link.token_hash, expires)
    return link.token


def link_owner(token: str) -> User | None:
    user_id = users.link_user(link_hash(token), time.time())
    user = users.get_user(user_id) if user_id is not None else None
    return user if user is not None and user.active else None


def set_password_with_link(token: str, password: str, confirm: str) -> User | None:
    user = link_owner(token)
    if user is None:
        return None
    hashed = hash_password(_checked(password, confirm))
    user_id = users.use_password_link(link_hash(token), time.time(), hashed)
    return users.admit(users.get_user(user_id)) if user_id is not None else None


def change_password(user_id: int, current: str, password: str, confirm: str) -> None:
    stored = users.password_hash(user_id)
    if stored and not verify_password(current, stored):
        msg = "The current password is wrong"
        raise WrongPasswordError(msg)
    hashed = hash_password(_checked(password, confirm))
    if not users.replace_password_hash(user_id, stored, hashed):
        msg = "Your password changed meanwhile: try again"
        raise PasswordRejectedError(msg)
