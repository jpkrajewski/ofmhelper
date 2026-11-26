# Copyright (C) 2026 jpkrajewski

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Role(StrEnum):
    ADMIN = "admin"
    USER = "user"
    VA = "va"
    MANAGER = "manager"

    @property
    def has_employer(self) -> bool:
        return self in (Role.VA, Role.MANAGER)


class StorageProvider(StrEnum):
    GDRIVE = "gdrive"
    DROPBOX = "dropbox"


class User(BaseModel):
    id: int
    email: str
    name: str = ""
    role: Role
    active: bool = True
    created_at: float
    last_login_at: float | None = None
    drive_connected: bool = False
    has_password: bool = False
    drive_root_id: str | None = None
    storage_provider: StorageProvider = StorageProvider.GDRIVE
    is_password_account: bool = False
    owner_id: int | None = None
    credential_stamp: str = ""
    sessions_ended_at: float | None = None
    last_seen_at: float | None = None

    @property
    def is_admin(self) -> bool:
        return self.role == Role.ADMIN

    @property
    def label(self) -> str:
        return self.name or self.email


class Standing(BaseModel):
    role: Role
    active: bool
    credential_stamp: str
    sessions_ended_at: float | None
    employer_active: bool | None


class SessionUser(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    email: str
    role: Role

    @property
    def is_admin(self) -> bool:
        return self.role == Role.ADMIN

    @property
    def actor(self) -> str:
        return str(self.id)


class Viewer(BaseModel):
    model_config = ConfigDict(frozen=True)

    user: SessionUser
    user_ids: frozenset[int]
    team_ids: frozenset[int]
    keys_of: int | None
    by: SessionUser | None = None
    label: str = ""
    acts: bool = False
    pages: Role | None = None

    @property
    def read_only(self) -> bool:
        return self.by is not None and not self.acts

    @property
    def access_role(self) -> Role:
        return self.pages or self.user.role

    @property
    def managing(self) -> bool:
        return self.pages == Role.MANAGER

    @property
    def actors(self) -> frozenset[str]:
        return frozenset(str(user_id) for user_id in self.user_ids)

    @property
    def team(self) -> frozenset[str]:
        return frozenset(str(user_id) for user_id in self.team_ids)

    @property
    def types_key(self) -> bool:
        return self.keys_of == self.user.id and not self.read_only


def _email(value: str) -> str:
    return value.strip().lower()


class UserForm(BaseModel):
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=320)
    role: Role = Role.USER
    name: str = Field(default="", max_length=255)
    owner_id: int | None = None

    @field_validator("email", mode="before")
    @classmethod
    def _normalized(cls, value: str) -> str:
        return _email(value)


class LinkedAccount(BaseModel):
    provider: str
    subject: str
    email: str = ""
    linked_at: float


class SavedKey(BaseModel):
    provider: str
    api_key: str


class SavedKeys(BaseModel):
    keys: list[SavedKey] = []

    def get(self, provider: str) -> str:
        return next((k.api_key for k in self.keys if k.provider == provider), "")


class PasswordForm(BaseModel):
    password: str = Field(min_length=1, max_length=256)
    confirm: str = Field(min_length=1, max_length=256)


class ApiKeysForm(BaseModel):
    keys: dict[str, Annotated[str, Field(max_length=512)]] = Field(default_factory=dict)
    remove: str | None = Field(default=None, max_length=32)

    @field_validator("keys")
    @classmethod
    def _typed(cls, value: dict[str, str]) -> dict[str, str]:
        return {name: key.strip() for name, key in value.items() if key.strip()}


def normalized_email(value: str) -> str:
    return _email(value)


class Identity(BaseModel):
    provider: str
    subject: str
    email: str = ""
    email_verified: bool = False
    name: str = ""
    refresh_token: str | None = None
