"""
ofmhelpers/web/stores/users.py

Who may sign in, and as what. Nobody signs themselves up: an admin adds an
email on /users. Then it signs in:

- with Google or Apple: by the provider's account id once linked, else by
  a verified email equal to the user's (linking it); an `OFM_ADMIN_EMAILS`
  address always gets in, as an admin (made on the spot: the way in on a
  fresh install, and back in if every admin was removed). The FIRST of
  them takes over the password account at their first sign-in -- its
  email becomes theirs, so the agency's phones, accounts and jobs (all
  the password account's since users exist) are theirs with nothing moved;
- with email + password: once the user set one through a one-time link
  (`password_link`, made on /users) or on /account. `APP_PASSWORD_ADMIN`
  opens the password account (typed as "admin", or by its email once
  taken over) and every `OFM_ADMIN_EMAILS` admin, for when nothing else
  works.

A deactivated user gets in nowhere.
"""

from __future__ import annotations

import hmac
import time
from functools import lru_cache
from typing import TYPE_CHECKING

from ofmhelpers.config import settings
from ofmhelpers.signin.passwords import (
    hash_password,
    link_hash,
    new_link_token,
    verify_password,
)
from ofmhelpers.web.db.repositories import UserRepository
from ofmhelpers.web.schemas import LinkedAccount, Role, User, UserForm
from ofmhelpers.web.schemas.users import normalized_email

if TYPE_CHECKING:
    from ofmhelpers.signin import Identity

# The password account's email (alembic c2d3e4f5a6b7 makes it).
PASSWORD_ADMIN = "admin"  # noqa: S105 -- an account name, not a password


class PasswordRejectedError(ValueError):
    """A new password not accepted; the message says why."""


@lru_cache(maxsize=1)
def _repository() -> UserRepository:
    return UserRepository()


def _user(row: dict | None) -> User | None:
    return User.model_validate(row) if row else None


def is_env_admin(email: str) -> bool:
    return normalized_email(email) in settings.signin.admin_emails


def get_user(user_id: int) -> User | None:
    return _user(_repository().get(user_id))


def by_email(email: str) -> User | None:
    return _user(_repository().by_email(normalized_email(email)))


def list_users() -> list[User]:
    return [User.model_validate(r) for r in _repository().list_all()]


def add_user(form: UserForm) -> User | None:
    """None when the email is already a user."""
    return _user(_repository().add(form.email, form.role, form.name))


def set_role(user_id: int, role: Role) -> User | None:
    return _user(_repository().update(user_id, role=role))


def set_active(user_id: int, *, active: bool) -> User | None:
    return _user(_repository().update(user_id, active=active))


def delete_user(user_id: int) -> bool:
    """Raises `OwnsDataError` while they own anything: deactivate instead."""
    return _repository().delete(user_id)


def _admit(
    user: User | None, name: str = "", refresh_token: str | None = None
) -> User | None:
    if user is None or not user.active:
        return None
    _repository().signed_in(user.id, name, refresh_token)
    return get_user(user.id)


def _claim_password_account(email: str, name: str) -> User | None:
    """The first `OFM_ADMIN_EMAILS` admin, at their first sign-in, becomes
    the password account (still unclaimed: its email is "admin")."""
    admins = settings.signin.admin_emails
    if not admins or normalized_email(email) != admins[0]:
        return None
    account = _password_admin()
    if account is None or account.email != PASSWORD_ADMIN:
        return None
    return _user(
        _repository().update(account.id, email=normalized_email(email), name=name)
    )


def _env_admin(email: str, name: str) -> User | None:
    """The `OFM_ADMIN_EMAILS` user for `email`, made (or, for the first of
    them, taken over from the password account) or restored as an active
    admin; None for any other email."""
    if not is_env_admin(email):
        return None
    user = by_email(email)
    if user is None:
        claimed = _claim_password_account(email, name)
        if claimed is not None:
            return claimed
        # None: a concurrent first sign-in made them a moment ago.
        added = _repository().add(normalized_email(email), Role.ADMIN, name)
        return _user(added) or by_email(email)
    if not user.is_admin or not user.active:
        return _user(_repository().update(user.id, role=Role.ADMIN, active=True))
    return user


# --- Google / Apple ------------------------------------------------------------


def sign_in_identity(identity: Identity) -> User | None:
    """The user a Google/Apple sign-in is, or None when it may not get in."""
    repo = _repository()
    linked = repo.user_for_identity(identity.provider, identity.subject)
    user = get_user(linked) if linked is not None else None
    if user is None and identity.email_verified and identity.email:
        user = _env_admin(identity.email, identity.name) or by_email(identity.email)
        if user is not None:
            repo.link_identity(
                user.id, identity.provider, identity.subject, identity.email
            )
    elif user is not None and is_env_admin(user.email):
        user = _env_admin(user.email, identity.name)
    return _admit(user, identity.name, identity.refresh_token)


def link_identity(user_id: int, identity: Identity) -> bool:
    """Links a Google/Apple account to a signed-in user (/account), keeping
    a Google refresh token for Drive. False when it belongs to someone else."""
    if not _repository().link_identity(
        user_id, identity.provider, identity.subject, identity.email
    ):
        return False
    _repository().signed_in(user_id, "", identity.refresh_token)
    return True


def unlink_identity(user_id: int, provider: str) -> None:
    _repository().unlink_identity(user_id, provider)


def linked_accounts(user_id: int) -> list[LinkedAccount]:
    return [LinkedAccount.model_validate(r) for r in _repository().identities(user_id)]


# --- email + password ------------------------------------------------------------


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
    user = by_email(email)
    stored = _repository().password_hash(user.id) if user else ""
    if verify_password(password, stored) and user is not None:
        return _admit(user)
    if _is_admin_password(password):
        if email == PASSWORD_ADMIN:
            return _admit(_password_admin())
        if is_env_admin(email):
            return _admit(_env_admin(email, ""))
    return None


def agency_id() -> int:
    """Who owns what is made outside any request (scripts, backfills): the
    password account (the agency): only it sees it."""
    user = _password_admin()
    if user is None:  # made when missing: only a lost race could leave none
        msg = "the password account could not be made"
        raise RuntimeError(msg)
    return user.id


def _password_admin() -> User | None:
    """The password account, made or restored as an active admin."""
    user = _user(_repository().password_account())
    if user is None:
        added = _repository().add(
            PASSWORD_ADMIN, Role.ADMIN, "Admin (password)", password_account=True
        )
        # None: a concurrent request made it a moment ago.
        return _user(added) or _user(_repository().password_account())
    if not user.is_admin or not user.active:
        return _user(_repository().update(user.id, role=Role.ADMIN, active=True))
    return user


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
    token, token_hash = new_link_token()
    expires = time.time() + settings.signin.password_link_ttl_s
    _repository().add_password_link(user_id, token_hash, expires)
    return token


def link_owner(token: str) -> User | None:
    """The active user an unexpired link is for."""
    user_id = _repository().link_user(link_hash(token), time.time())
    user = get_user(user_id) if user_id is not None else None
    return user if user is not None and user.active else None


def set_password_with_link(token: str, password: str, confirm: str) -> User | None:
    """Sets the password and signs in; None for a bad or used link. Raises
    `PasswordRejectedError` for a password not accepted."""
    user = link_owner(token)
    if user is None:
        return None
    _repository().set_password_hash(user.id, hash_password(_checked(password, confirm)))
    return _admit(user)


def change_password(user_id: int, current: str, password: str, confirm: str) -> None:
    """/account. `current` is asked only when there is one. Raises
    `PasswordRejectedError`."""
    stored = _repository().password_hash(user_id)
    if stored and not verify_password(current, stored):
        msg = "The current password is wrong"
        raise PasswordRejectedError(msg)
    _repository().set_password_hash(user_id, hash_password(_checked(password, confirm)))


# --- what a user keeps ---------------------------------------------------------


def set_drive_root(user_id: int, folder_id: str | None) -> None:
    """Their "ALL ACCOUNTS" folder in their own Drive (None: find it again)."""
    _repository().set_drive_root(user_id, folder_id)


def refresh_token(user_id: int) -> str:
    """The Google refresh token Drive uploads go up with; "" when none."""
    return _repository().refresh_token(user_id)


def api_keys(user_id: int) -> dict[str, str]:
    return _repository().api_keys(user_id)


def set_api_key(user_id: int, provider: str, api_key: str) -> None:
    _repository().set_api_key(user_id, provider, api_key)
