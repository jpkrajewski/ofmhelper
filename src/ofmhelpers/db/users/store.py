# Copyright (C) 2026 jpkrajewski

from functools import lru_cache

from ofmhelpers.config import settings
from ofmhelpers.db.encrypted import VaultError
from ofmhelpers.db.users.repository import UserRepository
from ofmhelpers.db.users.schema import (
    LinkedAccount,
    Role,
    Standing,
    StorageProvider,
    User,
    UserForm,
    normalized_email,
)
from ofmhelpers.log import get_logger
lazy from ofmhelpers.db.users.schema import Identity, SavedKeys

logger = get_logger(__name__)


@lru_cache(maxsize=1)
def _repository() -> UserRepository:
    return UserRepository()


def is_env_admin(email: str) -> bool:
    return normalized_email(email) in settings.signin.admin_emails


def get_user(user_id: int) -> User | None:
    return _repository().get(user_id)


def standing(user_id: int) -> Standing | None:
    return _repository().standing(user_id)


def get_user_uncached(user_id: int) -> User | None:
    return _repository().fresh(user_id)


def by_email(email: str) -> User | None:
    return _repository().by_email(normalized_email(email))


def list_users() -> list[User]:
    return _repository().list_all()


def add_user(form: UserForm) -> User | None:
    return _repository().add(
        form.email,
        form.role,
        form.name,
        owner_id=form.owner_id if form.role.has_employer else None,
    )


def set_role(user_id: int, role: Role) -> User | None:
    if role.has_employer:
        return _repository().update(user_id, {"role": role})
    return _repository().update(user_id, {"role": role, "owner_id": None})


def set_owner(user_id: int, owner_id: int | None) -> User | None:
    return _repository().update(user_id, {"owner_id": owner_id})


def vas_of(owner_id: int) -> list[User]:
    return [
        user
        for user in _repository().list_all()
        if user.role == Role.VA and user.owner_id == owner_id
    ]


def set_active(user_id: int, *, active: bool) -> User | None:
    return _repository().update(user_id, {"active": active})


def delete_user(user_id: int) -> bool:
    return _repository().delete(user_id)


def admit(
    user: User | None,
    name: str = "",
    refresh_token: str | None = None,
) -> User | None:
    if user is None or not user.active:
        return None
    _repository().signed_in(user.id, name, refresh_token)
    return get_user(user.id)


def _claim_password_account(email: str, name: str) -> User | None:
    admins = settings.signin.admin_emails
    if not admins or normalized_email(email) != admins[0]:
        return None
    account = agency_admin()
    if account is None or account.email != settings.signin.password_admin:
        return None
    return _repository().update(
        account.id,
        {"email": normalized_email(email), "name": name},
    )


def env_admin(email: str, name: str) -> User | None:
    if not is_env_admin(email):
        return None
    return _claim_password_account(email, name) or agency_admin()


def sign_in_identity(identity: Identity) -> User | None:
    repo = _repository()
    if identity.provider == settings.signin.owner_provider:
        return admit(password_admin())
    if identity.email_verified and is_env_admin(identity.email):
        user = env_admin(identity.email, identity.name)
        if user is not None:
            repo.link_identity(
                user.id,
                identity.provider,
                identity.subject,
                identity.email,
            )
        return admit(user, identity.name, identity.refresh_token)
    linked = repo.user_for_identity(identity.provider, identity.subject)
    user = get_user(linked) if linked is not None else None
    if user is None and identity.email_verified and identity.email:
        user = by_email(identity.email)
        if user is not None:
            repo.link_identity(
                user.id,
                identity.provider,
                identity.subject,
                identity.email,
            )
    elif user is not None and is_env_admin(user.email):
        user = env_admin(user.email, identity.name)
    return admit(user, identity.name, identity.refresh_token)


def link_identity(user_id: int, identity: Identity) -> bool:
    if identity.provider == settings.signin.owner_provider:
        return False
    if not _repository().link_identity(
        user_id,
        identity.provider,
        identity.subject,
        identity.email,
    ):
        return False
    _repository().signed_in(user_id, "", identity.refresh_token)
    return True


def unlink_identity(user_id: int, provider: str) -> None:
    _repository().unlink_identity(user_id, provider)


def linked_accounts(user_id: int) -> list[LinkedAccount]:
    return _repository().identities(user_id)


def password_account() -> User | None:
    return _repository().password_account()


def password_hash(user_id: int) -> str:
    return _repository().password_hash(user_id)


def set_password_hash(user_id: int, hashed: str) -> None:
    _repository().set_password_hash(user_id, hashed)


def replace_password_hash(user_id: int, expected: str, hashed: str) -> bool:
    return _repository().replace_password_hash(user_id, expected, hashed)


def add_password_link(user_id: int, token_hash: str, expires: float) -> None:
    _repository().add_password_link(user_id, token_hash, expires)


def use_password_link(token_hash: str, now: float, hashed: str) -> int | None:
    return _repository().use_password_link(token_hash, now, hashed)


def link_user(token_hash: str, now: float) -> int | None:
    return _repository().link_user(token_hash, now)


def agency_id() -> int:
    user = _repository().password_account() or password_admin()
    if user is None:
        msg = "the password account could not be made"
        raise RuntimeError(msg)
    return user.id


def agency_admin() -> User | None:
    user = _repository().password_account()
    if user is None:
        return password_admin()
    if not user.is_admin:
        return _repository().update(user.id, {"role": Role.ADMIN})
    return user


def password_admin() -> User | None:
    user = _repository().password_account()
    if user is None:
        added = _repository().add(
            settings.signin.password_admin,
            Role.ADMIN,
            "Admin (password)",
            password_account=True,
        )
        return added or _repository().password_account()
    if not user.is_admin or not user.active:
        return _repository().update(user.id, {"role": Role.ADMIN, "active": True})
    return user


def set_drive_root(user_id: int, folder_id: str | None) -> None:
    _repository().set_drive_root(user_id, folder_id)


def set_storage_provider(user_id: int, provider: StorageProvider) -> None:
    _repository().set_storage_provider(user_id, provider)


def refresh_token(user_id: int) -> str:
    try:
        return _repository().refresh_token(user_id)
    except VaultError as exc:
        logger.warning(
            "user %s: Google token unreadable: %s",
            user_id,
            exc,
            exc_info=True,
        )
        return ""


def api_keys(user_id: int) -> SavedKeys:
    try:
        return _repository().api_keys(user_id)
    except VaultError as exc:
        logger.warning(
            "user %s: saved API keys unreadable: %s",
            user_id,
            exc,
            exc_info=True,
        )
        return SavedKeys()


def set_api_key(user_id: int, provider: str, api_key: str) -> None:
    _repository().set_api_key(user_id, provider, api_key)


def keys_in_slot(slot: str) -> list[str]:
    try:
        return _repository().keys_in_slot(slot)
    except VaultError:
        logger.warning("saved %s keys unreadable: vault key", slot, exc_info=True)
        return []


def end_sessions(user_id: int) -> None:
    _repository().end_sessions(user_id)


def seen(user_id: int, at: float) -> None:
    _repository().seen(user_id, at)


def end_idle_sessions(user_id: int) -> bool:
    return _repository().end_idle_sessions(user_id)


def idle_vas(before: float) -> list[User]:
    return _repository().idle_vas(before)
