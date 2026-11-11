"""What /users does behind its routes (web/routers/admin/users.py): the
list, a user added with their one-time set-password link, a new link, a VA's
owner, access, removal. A role is picked once, when the user is added, and
never changed after (owner's rule 2026-11-12): what someone made, whose keys
paid and who saw it all hang on it. A wrong pick: remove or deactivate, add
again. Nobody can lock themselves out from here: `other` refuses
the admin's own row."""

from pydantic import BaseModel, ConfigDict, SkipValidation, ValidationError

from ofmhelpers.config import settings
from ofmhelpers.db.repositories import OwnsDataError
from ofmhelpers.db.stores import users
from ofmhelpers.features.refusals import NotFoundError, RefusedError
from ofmhelpers.features.signin import credentials
from ofmhelpers.integrations.signin import Provider, configured
from ofmhelpers.schemas import Role, User, UserForm

NO_USER = "No such user"


class Shown(BaseModel):
    """What the page shows on top after a write: an error, or the
    set-password link made for `link_for`."""

    error: str | None = None
    message: str | None = None
    link_for: User | None = None
    link: str | None = None
    status_code: int = 200


class PageView(BaseModel):
    """/users: each field is a name `users.html` reads."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    users: list[User]
    me: int
    roles: list[Role]
    # Who a VA can work for: every active user that is not a VA.
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
    return [u for u in users.list_users() if u.active and u.role != Role.VA]


def _owner(owner_id: int | None) -> int | None:
    """A VA's owner as picked on the page: nobody, or a user who may own."""
    if owner_id is None:
        return None
    if owner_id not in {u.id for u in owners()}:
        msg = "Pick an active user, not a VA, as the owner"
        raise RefusedError(msg)
    return owner_id


def _user(user_id: int) -> User:
    user = users.get_user(user_id)
    if user is None:
        raise NotFoundError(NO_USER)
    return user


def other(user_id: int, me_id: int) -> User:
    """A user that is not the one signed in (no locking yourself out)."""
    if user_id == me_id:
        msg = "Not on your own account"
        raise RefusedError(msg)
    return _user(user_id)


def _with_link(user: User, site: str) -> Shown:
    """`site`: this request's base URL, used when `APP_BASE_URL` is not set."""
    base = settings.web.base_url or site
    link = f"{base.rstrip('/')}/password/{credentials.password_link(user.id)}"
    return Shown(link_for=user, link=link)


def _va_signs_in(va: User) -> str:
    """How a VA gets in, for the notice after one is added or re-keyed."""
    owner = users.get_user(va.owner_id) if va.owner_id is not None else None
    employer = owner.email if owner is not None else "their employer's email"
    return (
        f'{va.label} signs in with "Login as Virtual Assistant": {employer}'
        " and the password you set."
    )


def add(
    email: str,
    role: Role,
    name: str,
    site: str,
    owner_id: int | None = None,
    password: str = "",
) -> Shown:
    """A user with their one-time set-password link; a VA with an employer
    (owner) and the password the admin typed: the VA signs in with "Login as
    Virtual Assistant" (employer's email + that password), no link, nothing
    for the VA to set up."""
    try:
        form = UserForm(email=email, role=role, name=name, owner_id=_owner(owner_id))
    except ValidationError:
        return Shown(error=f"{email!r} is not an email address", status_code=400)
    if form.role == Role.VA:
        if form.owner_id is None:
            return Shown(error="Pick the VA's employer", status_code=400)
        try:
            credentials.check_va_password(form.owner_id, password)
        except credentials.PasswordRejectedError as exc:
            return Shown(error=str(exc), status_code=400)
    user = users.add_user(form)
    if user is None:
        return Shown(error=f"{form.email} is already a user", status_code=409)
    if user.role == Role.VA and user.owner_id is not None:
        credentials.set_va_password(user.id, user.owner_id, password)
        return Shown(message=_va_signs_in(user))
    return _with_link(user, site)


def set_va_password(user: User, password: str) -> Shown:
    """A new password for a VA (a forgotten one, or one to take back)."""
    if user.role != Role.VA or user.owner_id is None:
        msg = "Only a VA with an employer has a password set here"
        raise RefusedError(msg)
    try:
        credentials.set_va_password(user.id, user.owner_id, password)
    except credentials.PasswordRejectedError as exc:
        return Shown(error=str(exc), status_code=400)
    return Shown(message=f"Password saved. {_va_signs_in(user)}")


def new_link(user_id: int, site: str) -> Shown:
    """A new set-password link (the old one stops working): a first
    password, or a forgotten one. Not for a VA: an admin sets theirs."""
    user = _user(user_id)
    if user.role == Role.VA:
        msg = "A VA has no password link: set the password here on Users"
        raise RefusedError(msg)
    return _with_link(user, site)


def set_owner(user: User, owner_id: int | None) -> None:
    """Whose VA `user` is (their employer): the owner sees what the VA
    makes, the VA's runs go out on the owner's saved API keys, and the VA
    signs in with the owner's email. A VA always has one."""
    if user.role != Role.VA:
        msg = "Only a VA has an owner"
        raise RefusedError(msg)
    picked = _owner(owner_id)
    if picked is None:
        msg = "A VA needs an employer: pick one"
        raise RefusedError(msg)
    users.set_owner(user.id, picked)


def set_active(user: User, active: bool) -> None:
    users.set_active(user.id, active=active)


def remove(user: User) -> Shown | None:
    """Their sign-ins and keys go with them. Refused (a `Shown`) while they
    own jobs (a sign-in in the action log is one: `jobs.user_id`), phones,
    accounts or watched profiles: deactivating keeps those."""
    try:
        users.delete_user(user.id)
    except OwnsDataError:
        return Shown(
            error=f"{user.email} still owns jobs, phones, accounts or watched"
            " profiles: deactivate them instead (nothing of theirs is lost).",
            status_code=409,
        )
    return None
