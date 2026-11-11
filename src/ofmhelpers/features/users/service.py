"""What /users does behind its routes (web/routers/admin/users.py): the
list, a user added with their one-time set-password link, a new link, role,
access, removal. Nobody can lock themselves out from here: `other` refuses
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
    link_for: User | None = None
    link: str | None = None
    status_code: int = 200


class PageView(BaseModel):
    """/users: each field is a name `users.html` reads."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    users: list[User]
    me: int
    roles: list[Role]
    providers: SkipValidation[list[Provider]]
    env_admins: list[str]
    link_for: User | None
    link: str | None
    link_days: int
    error: str | None


def page_view(me_id: int, shown: Shown) -> PageView:
    return PageView(
        users=users.list_users(),
        me=me_id,
        roles=list(Role),
        providers=configured(),
        env_admins=settings.signin.admin_emails,
        link_for=shown.link_for,
        link=shown.link,
        link_days=settings.signin.password_link_ttl_s // 86400,
        error=shown.error,
    )


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


def add(email: str, role: Role, name: str, site: str) -> Shown:
    try:
        form = UserForm(email=email, role=role, name=name)
    except ValidationError:
        return Shown(error=f"{email!r} is not an email address", status_code=400)
    user = users.add_user(form)
    if user is None:
        return Shown(error=f"{form.email} is already a user", status_code=409)
    return _with_link(user, site)


def new_link(user_id: int, site: str) -> Shown:
    """A new set-password link (the old one stops working): a first
    password, or a forgotten one."""
    return _with_link(_user(user_id), site)


def set_role(user: User, role: Role) -> None:
    users.set_role(user.id, role)


def set_active(user: User, active: bool) -> None:
    users.set_active(user.id, active=active)


def remove(user: User) -> Shown | None:
    """Their sign-ins and keys go with them. Refused (a `Shown`) while they
    own phones, accounts or watched profiles: deactivating keeps those."""
    try:
        users.delete_user(user.id)
    except OwnsDataError:
        return Shown(
            error=f"{user.email} still owns phones, accounts or watched profiles:"
            " deactivate them instead (nothing of theirs is lost).",
            status_code=409,
        )
    return None
