# Copyright (C) 2026 jpkrajewski

from pydantic import BaseModel, ConfigDict, SkipValidation, ValidationError

from ofmhelpers.config import settings
from ofmhelpers.db.users import store as users
from ofmhelpers.db.users.repository import OwnsDataError
from ofmhelpers.db.users.schema import Role, User, UserForm
from ofmhelpers.features.refusals import NotFoundError, RefusedError
from ofmhelpers.features.storage import jobs as storage_jobs
from ofmhelpers.features.users import credentials
from ofmhelpers.integrations.signin import Provider, configured

NO_USER = "No such user"


class Shown(BaseModel):
    error: str | None = None
    message: str | None = None
    link_for: User | None = None
    link: str | None = None
    status_code: int = 200


class PageView(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    users: list[User]
    me: int
    roles: list[Role]
    owners: list[User]
    providers: SkipValidation[list[Provider]]
    env_admins: list[str]
    link_for: User | None
    link: str | None
    link_days: int
    error: str | None
    message: str | None
    password_min: int


def page_view(me_id: int, shown: Shown) -> PageView:
    return PageView(
        users=users.list_users(),
        me=me_id,
        roles=list(Role),
        owners=owners(),
        providers=configured(),
        env_admins=settings.signin.admin_emails,
        link_for=shown.link_for,
        link=shown.link,
        link_days=settings.signin.password_link_ttl_s // 86400,
        error=shown.error,
        message=shown.message,
        password_min=settings.signin.password_min_length,
    )


def owners() -> list[User]:
    return [u for u in users.list_users() if u.active and not u.role.has_employer]


def _owner(owner_id: int | None) -> int | None:
    if owner_id is None:
        return None
    if owner_id not in {u.id for u in owners()}:
        msg = "Pick an active user, not a VA or a manager, as the owner"
        raise RefusedError(msg)
    return owner_id


def _user(user_id: int) -> User:
    user = users.get_user(user_id)
    if user is None:
        raise NotFoundError(NO_USER)
    return user


def other(user_id: int, me_id: int) -> User:
    if user_id == me_id:
        msg = "Not on your own account"
        raise RefusedError(msg)
    return _user(user_id)


def _with_link(user: User, site: str) -> Shown:
    base = settings.web.base_url or site
    link = f"{base.rstrip('/')}/password/{credentials.password_link(user.id)}"
    return Shown(link_for=user, link=link)


def _va_signs_in(va: User) -> str:
    owner = users.get_user(va.owner_id) if va.owner_id is not None else None
    employer = owner.email if owner is not None else "their employer's email"
    return (
        f'{va.label} signs in with "Login as Virtual Assistant": {employer}'
        " and the password you set."
    )


def _make_va_folder(va: User) -> None:
    if va.owner_id is None or not storage_jobs.enabled():
        return
    storage_jobs.queue_sync(va.owner_id)


def add(
    email: str,
    role: Role,
    name: str,
    site: str,
    owner_id: int | None = None,
    password: str = "",
) -> Shown:
    try:
        form = UserForm(email=email, role=role, name=name, owner_id=_owner(owner_id))
    except ValidationError:  # silent: shown on the page
        return Shown(error=f"{email!r} is not an email address", status_code=400)
    if form.role == Role.VA:
        if form.owner_id is None:
            return Shown(error="Pick the VA's employer", status_code=400)
        try:
            credentials.check_va_password(form.owner_id, password)
        except credentials.PasswordRejectedError as exc:  # silent: shown on the page
            return Shown(error=str(exc), status_code=400)
    if form.role == Role.MANAGER and form.owner_id is None:
        return Shown(error="Pick the manager's employer", status_code=400)
    user = users.add_user(form)
    if user is None:
        return Shown(error=f"{form.email} is already a user", status_code=409)
    if user.role == Role.VA and user.owner_id is not None:
        credentials.set_va_password(user.id, user.owner_id, password)
        _make_va_folder(user)
        return Shown(message=_va_signs_in(user))
    return _with_link(user, site)


def set_va_password(user: User, password: str) -> Shown:
    if user.role != Role.VA or user.owner_id is None:
        msg = "Only a VA with an employer has a password set here"
        raise RefusedError(msg)
    try:
        credentials.set_va_password(user.id, user.owner_id, password)
    except credentials.PasswordRejectedError as exc:  # silent: shown on the page
        return Shown(error=str(exc), status_code=400)
    return Shown(message=f"Password saved. {_va_signs_in(user)}")


def new_link(user_id: int, site: str) -> Shown:
    user = _user(user_id)
    if user.role == Role.VA:
        msg = "A VA has no password link: set the password here on Users"
        raise RefusedError(msg)
    return _with_link(user, site)


def set_owner(user: User, owner_id: int | None) -> None:
    if not user.role.has_employer:
        msg = "Only a VA or a manager has an employer"
        raise RefusedError(msg)
    if user.role == Role.VA and user.owner_id is not None:
        msg = (
            "A VA's employer is set when they are added. To change it,"
            " deactivate them and add them again."
        )
        raise RefusedError(msg)
    picked = _owner(owner_id)
    if picked is None:
        msg = "Pick an employer"
        raise RefusedError(msg)
    moved = users.set_owner(user.id, picked)
    if moved is not None and moved.role == Role.VA:
        _make_va_folder(moved)


def set_active(user: User, active: bool) -> None:
    users.set_active(user.id, active=active)


def remove(user: User) -> Shown | None:
    try:
        users.delete_user(user.id)
    except OwnsDataError:  # silent: shown on the page
        return Shown(
            error=f"{user.email} still owns jobs, phones, accounts or watched"
            " profiles: deactivate them instead (nothing of theirs is lost).",
            status_code=409,
        )
    return None
