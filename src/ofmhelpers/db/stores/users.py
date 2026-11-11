"""
ofmhelpers/web/stores/users.py

Who may sign in, and as what. Nobody signs themselves up: an admin adds an
email on /users. Then it signs in:

- with Google or Apple: by the provider's account id once linked, else by
  a verified email equal to the user's (linking it);
- with email + password: once the user set one through a one-time link
  (made on /users) or on /account (`features/signin/credentials.py` checks
  and sets passwords; this store keeps their hashes);
- as the app owner (signin/owner.py): `APP_PASSWORD_ADMIN` alone.

The agency admin is ONE user, the password account, however it signs in
(user decision 2026-10-22): the app owner, "admin" or any `OFM_ADMIN_EMAILS`
address with the admin password (or the account's own), Google/Apple with
any `OFM_ADMIN_EMAILS` address. The first address names it at its first
sign-in (the agency's phones, accounts and jobs stay where they are); no
separate admin users are made for the others. Several people signed in at
once that way are the same user: same data, same Drive.

A deactivated user gets in nowhere.
"""

from functools import lru_cache

from ofmhelpers.config import settings
from ofmhelpers.db.encrypted import VaultError
from ofmhelpers.db.repositories import UserRepository
from ofmhelpers.log import get_logger
from ofmhelpers.schemas import LinkedAccount, Role, User, UserForm
from ofmhelpers.schemas.users import normalized_email
lazy from ofmhelpers.schemas.users import Identity, SavedKeys

logger = get_logger(__name__)

# The password account's email (alembic c2d3e4f5a6b7 makes it).
PASSWORD_ADMIN = "admin"  # noqa: S105 -- an account name, not a password
# The app owner sign-in (the admin password alone, signin/owner.py).
OWNER_PROVIDER = "owner"


@lru_cache(maxsize=1)
def _repository() -> UserRepository:
    return UserRepository()


def is_env_admin(email: str) -> bool:
    return normalized_email(email) in settings.signin.admin_emails


def get_user(user_id: int) -> User | None:
    return _repository().get(user_id)


def by_email(email: str) -> User | None:
    return _repository().by_email(normalized_email(email))


def list_users() -> list[User]:
    return _repository().list_all()


def add_user(form: UserForm) -> User | None:
    """None when the email is already a user."""
    return _repository().add(form.email, form.role, form.name)


def set_role(user_id: int, role: Role) -> User | None:
    return _repository().update(user_id, role=role)


def set_active(user_id: int, *, active: bool) -> User | None:
    return _repository().update(user_id, active=active)


def delete_user(user_id: int) -> bool:
    """Raises `OwnsDataError` while they own anything: deactivate instead."""
    return _repository().delete(user_id)


def admit(
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
    account = password_admin()
    if account is None or account.email != PASSWORD_ADMIN:
        return None
    return _repository().update(account.id, email=normalized_email(email), name=name)


def env_admin(email: str, name: str) -> User | None:
    """The admin every `OFM_ADMIN_EMAILS` address is: the password account
    (the agency), one user however it signs in -- Google, Apple, an admin
    email + password, the admin password alone (user decision 2026-10-22).
    The first address names it at its first sign-in; None for any other
    email."""
    if not is_env_admin(email):
        return None
    return _claim_password_account(email, name) or password_admin()


# --- Google / Apple ------------------------------------------------------------


def sign_in_identity(identity: Identity) -> User | None:
    """The user a Google/Apple sign-in is, or None when it may not get in."""
    repo = _repository()
    if identity.provider == OWNER_PROVIDER:
        # The admin password (signin/owner.py): the agency admin.
        return admit(password_admin())
    if identity.email_verified and is_env_admin(identity.email):
        # An admin address is the agency admin, even if this Google/Apple
        # account was linked to another user before.
        user = env_admin(identity.email, identity.name)
        if user is not None:
            repo.link_identity(
                user.id, identity.provider, identity.subject, identity.email
            )
        return admit(user, identity.name, identity.refresh_token)
    linked = repo.user_for_identity(identity.provider, identity.subject)
    user = get_user(linked) if linked is not None else None
    if user is None and identity.email_verified and identity.email:
        user = by_email(identity.email)
        if user is not None:
            repo.link_identity(
                user.id, identity.provider, identity.subject, identity.email
            )
    elif user is not None and is_env_admin(user.email):
        user = env_admin(user.email, identity.name)
    return admit(user, identity.name, identity.refresh_token)


def link_identity(user_id: int, identity: Identity) -> bool:
    """Links a Google/Apple account to a signed-in user (/account), keeping
    a Google refresh token for Drive. False when it belongs to someone else."""
    if identity.provider == OWNER_PROVIDER:
        return False
    if not _repository().link_identity(
        user_id, identity.provider, identity.subject, identity.email
    ):
        return False
    _repository().signed_in(user_id, "", identity.refresh_token)
    return True


def unlink_identity(user_id: int, provider: str) -> None:
    _repository().unlink_identity(user_id, provider)


def linked_accounts(user_id: int) -> list[LinkedAccount]:
    return _repository().identities(user_id)


# --- email + password ------------------------------------------------------------


def password_account() -> User | None:
    """The password account as it is: nothing made or restored."""
    return _repository().password_account()


def password_hash(user_id: int) -> str:
    """The stored hash; "" without a password."""
    return _repository().password_hash(user_id)


def set_password_hash(user_id: int, hashed: str) -> None:
    _repository().set_password_hash(user_id, hashed)


def add_password_link(user_id: int, token_hash: str, expires: float) -> None:
    """Keeps a one-time set-password link's hash (the previous one stops
    working)."""
    _repository().add_password_link(user_id, token_hash, expires)


def link_user(token_hash: str, now: float) -> int | None:
    """Whose unexpired, unused link `token_hash` is."""
    return _repository().link_user(token_hash, now)


def agency_id() -> int:
    """Who owns what is made outside any request (scripts, backfills): the
    password account (the agency): only it sees it. Made when missing, but
    never reactivated: only the admin password does that (break-glass)."""
    user = _repository().password_account() or password_admin()
    if user is None:  # only a lost race could leave none
        msg = "the password account could not be made"
        raise RuntimeError(msg)
    return user.id


def password_admin() -> User | None:
    """The password account, made or restored as an active admin."""
    user = _repository().password_account()
    if user is None:
        added = _repository().add(
            PASSWORD_ADMIN, Role.ADMIN, "Admin (password)", password_account=True
        )
        # None: a concurrent request made it a moment ago.
        return added or _repository().password_account()
    if not user.is_admin or not user.active:
        return _repository().update(user.id, role=Role.ADMIN, active=True)
    return user


# --- what a user keeps ---------------------------------------------------------


def set_drive_root(user_id: int, folder_id: str | None) -> None:
    """Their "ALL ACCOUNTS" folder in their own Drive (None: find it again)."""
    _repository().set_drive_root(user_id, folder_id)


def refresh_token(user_id: int) -> str:
    """The Google refresh token Drive uploads go up with; "" when none, or
    when the vault key cannot read it: not connected, never a crash."""
    try:
        return _repository().refresh_token(user_id)
    except VaultError as exc:
        logger.warning("user %s: Google token unreadable: %s", user_id, exc)
        return ""


def api_keys(user_id: int) -> SavedKeys:
    """The user's saved keys; none when the vault key cannot read them: the
    forms open with their key box empty, never a crash."""
    try:
        return _repository().api_keys(user_id)
    except VaultError as exc:
        logger.warning("user %s: saved API keys unreadable: %s", user_id, exc)
        return SavedKeys()


def set_api_key(user_id: int, provider: str, api_key: str) -> None:
    _repository().set_api_key(user_id, provider, api_key)
