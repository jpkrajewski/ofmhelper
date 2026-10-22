"""Who signs in (/users, /login) and what a session knows about them."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Enough to catch a typo; Google decides what is a real account.
_EMAIL = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class Role(StrEnum):
    """Everyone sees only what is theirs; an admin also manages /users and
    the admin-only tools."""

    ADMIN = "admin"
    USER = "user"


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
    # Changes with the password: a session signed in under another one is
    # out (middleware/auth.py).
    credential_stamp: str = ""

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


def _email(value: str) -> str:
    return value.strip().lower()


class UserForm(BaseModel):
    """/users "Add": a Gmail (any Google account) and its role."""

    email: str = Field(pattern=_EMAIL, max_length=320)
    role: Role = Role.USER
    name: str = Field(default="", max_length=255)

    @field_validator("email", mode="before")
    @classmethod
    def _normalized(cls, value: str) -> str:
        return _email(value)


class RoleForm(BaseModel):
    role: Role


class LinkedAccount(BaseModel):
    """A Google or Apple account linked to a user."""

    provider: str
    subject: str
    email: str = ""
    linked_at: float


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
