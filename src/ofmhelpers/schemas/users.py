"""Who signs in (/users, /login) and what a session knows about them."""

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Enough to catch a typo; Google decides what is a real account.
_EMAIL = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class Role(StrEnum):
    """Everyone sees only what is theirs; an admin also manages /users and
    the admin-only tools; a VA works in a few tools for their owner
    (`User.owner_id`), who sees what the VA makes. What each role may open:
    `auth/access.py`."""

    ADMIN = "admin"
    USER = "user"
    VA = "va"


class User(BaseModel):
    """A `users` row, without its secrets."""

    id: int
    email: str
    name: str = ""
    role: Role
    active: bool = True
    created_at: float
    last_login_at: float | None = None
    # A Google refresh token is stored: Drive uploads can go up as them.
    drive_connected: bool = False
    has_password: bool = False
    drive_root_id: str | None = None
    is_password_account: bool = False
    # A VA's owner: whose keys the VA runs on, whose store the VA's files
    # land in, and who sees the VA's work. None for everyone else.
    owner_id: int | None = None
    # Changes with the password: a session signed in under another one is
    # out (middleware/auth.py).
    credential_stamp: str = ""
    # When they last pressed Logout (epoch seconds): a session signed in
    # before it is out, wherever it is (middleware/auth.py). None = never.
    sessions_ended_at: float | None = None
    # Their last action (epoch seconds), to the minute
    # (features/users/presence.py). None = not seen yet.
    last_seen_at: float | None = None

    @property
    def is_admin(self) -> bool:
        return self.role == Role.ADMIN

    @property
    def label(self) -> str:
        return self.name or self.email


class SessionUser(BaseModel):
    """What the signed cookie holds about who is signed in."""

    model_config = ConfigDict(frozen=True)

    id: int
    email: str
    role: Role

    @property
    def is_admin(self) -> bool:
        return self.role == Role.ADMIN

    @property
    def actor(self) -> str:
        """What a job they run stores as its `actor`."""
        return str(self.id)


class Viewer(BaseModel):
    """Who a request acts as, built once per request by the auth middleware
    (`features/users/viewing.py` `viewer_for`) and read by every dependency
    in `web/dependencies.py`. Normally the person signed in. An admin
    viewing as someone (`by` set) is that person for every read, and may
    change nothing (`read_only`), unless that someone is the admin's own VA
    (`acts`): the VA works for them on their keys, so what the admin does
    there is done as the VA."""

    model_config = ConfigDict(frozen=True)

    # The person the pages are shown for: whose rows, jobs and role.
    user: SessionUser
    # Whose jobs they see: their own, and their VAs'.
    user_ids: frozenset[int]
    # The people they work with: an owner and all of that owner's VAs, the
    # same set for each of them. What a team shares on purpose: /clone's
    # Last used / Most used subjects (owner 2026-11-14: a VA gets the same
    # ones). Never whose jobs they see: that is `user_ids`.
    team_ids: frozenset[int]
    # Whose saved keys their runs go out on (a VA: the owner's; a VA with
    # no owner: nobody's).
    keys_of: int | None
    # The admin looking through their eyes, when "view as" is on.
    by: SessionUser | None = None
    # The name the "Viewing as" banner shows.
    label: str = ""
    # The admin viewing may also act: the person viewed is their own VA.
    acts: bool = False

    @property
    def read_only(self) -> bool:
        return self.by is not None and not self.acts

    @property
    def actors(self) -> frozenset[str]:
        """`user_ids` as the job reads take them (`db/stores/jobs.py`
        `Readers`): text, like the `actor` a job is started under."""
        return frozenset(str(user_id) for user_id in self.user_ids)

    @property
    def team(self) -> frozenset[str]:
        """`team_ids` as text, like `actors`."""
        return frozenset(str(user_id) for user_id in self.team_ids)

    @property
    def types_key(self) -> bool:
        """Whether their forms show the API key field: not a VA's (the
        owner's key is used and never shown), not while viewing as."""
        return self.keys_of == self.user.id and not self.read_only


def _email(value: str) -> str:
    return value.strip().lower()


class UserForm(BaseModel):
    """/users "Add": a Gmail (any Google account) and its role."""

    email: str = Field(pattern=_EMAIL, max_length=320)
    role: Role = Role.USER
    name: str = Field(default="", max_length=255)
    owner_id: int | None = None

    @field_validator("email", mode="before")
    @classmethod
    def _normalized(cls, value: str) -> str:
        return _email(value)


class LinkedAccount(BaseModel):
    """A Google or Apple account linked to a user."""

    provider: str
    subject: str
    email: str = ""
    linked_at: float


class SavedKey(BaseModel):
    """A user's own key for one provider (/account)."""

    provider: str
    api_key: str


class SavedKeys(BaseModel):
    """Every key a user saved, one per provider."""

    keys: list[SavedKey] = []

    def get(self, provider: str) -> str:
        """`provider`'s key; "" without one."""
        return next((k.api_key for k in self.keys if k.provider == provider), "")


class PasswordForm(BaseModel):
    """Setting a password (from a link, or /account). Length is checked
    against `settings.signin.password_min_length` by the store."""

    password: str = Field(min_length=1, max_length=256)
    confirm: str = Field(min_length=1, max_length=256)


class ApiKeysForm(BaseModel):
    """/account: a user's own keys, one form for every provider. A key typed
    in is saved; a blank field leaves that provider's key as it is;
    `remove` names the one provider whose key goes."""

    keys: dict[str, Annotated[str, Field(max_length=512)]] = Field(default_factory=dict)
    remove: str | None = Field(default=None, max_length=32)

    @field_validator("keys")
    @classmethod
    def _typed(cls, value: dict[str, str]) -> dict[str, str]:
        return {name: key.strip() for name, key in value.items() if key.strip()}


def normalized_email(value: str) -> str:
    """How an email is stored and compared: trimmed, lower case."""
    return _email(value)


class Identity(BaseModel):
    """Who the provider says signed in."""

    provider: str
    # The provider's stable id for the account: what links it to a user.
    subject: str
    email: str = ""
    email_verified: bool = False
    name: str = ""
    # Google only: lets Drive uploads go up as this person.
    refresh_token: str | None = None
