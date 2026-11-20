import re
from datetime import UTC, datetime

from pydantic import BaseModel, computed_field, field_validator, model_validator


class ApplicationFields(BaseModel):
    first_name: str
    last_name: str
    email: str
    phone: str = ""
    telegram_handle: str = ""
    instagram_handle: str = ""
    monthly_revenue: str = ""
    experience_level: str = ""
    goals: str = ""


_LINK = re.compile(r"https?://|www\.|\[/?url|<a\s|\[/?link", re.IGNORECASE)
_TELEGRAM = re.compile(r"@?[A-Za-z][A-Za-z0-9_]{4,31}")
_INSTAGRAM = re.compile(r"@?[A-Za-z0-9._]{1,30}")
_PHONE = re.compile(r"\+?[\d\s().-]{7,25}")
_MAX_GOALS = 2000
_MAX_NAME = 60
_MAX_PHONE_DIGITS = 20


class ApplicationForm(ApplicationFields):
    @field_validator("*", mode="after")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()

    @field_validator("first_name", "last_name", mode="after")
    @classmethod
    def _required(cls, v: str) -> str:
        if not v:
            msg = "must not be blank"
            raise ValueError(msg)
        return v

    @field_validator("email", mode="after")
    @classmethod
    def _looks_like_an_address(cls, v: str) -> str:
        local, _, domain = v.partition("@")
        if not local or "." not in domain:
            msg = "must be an email address"
            raise ValueError(msg)
        return v

    @model_validator(mode="after")
    def _one_direct_channel(self) -> ApplicationForm:
        if not self.phone and not self.telegram_handle:
            msg = "give a phone number or a Telegram handle"
            raise ValueError(msg)
        return self

    def spam_reason(self) -> str | None:
        values = self.model_dump()
        if any(_LINK.search(v) for v in values.values()):
            return "a link in a field"
        checks = (
            ("telegram_handle", _TELEGRAM),
            ("instagram_handle", _INSTAGRAM),
            ("phone", _PHONE),
        )
        for field, pattern in checks:
            value = values[field]
            if value and not pattern.fullmatch(value):
                return f"{field} is not one"
        if self.phone and sum(c.isdigit() for c in self.phone) > _MAX_PHONE_DIGITS:
            return "phone is not one"
        if max(len(self.first_name), len(self.last_name)) > _MAX_NAME:
            return "name too long"
        if len(self.goals) > _MAX_GOALS:
            return "goals too long"
        return None


class Application(ApplicationFields):
    id: str
    created_at: float

    @computed_field  # type: ignore[prop-decorator]
    @property
    def created_at_display(self) -> str:
        return datetime.fromtimestamp(self.created_at, UTC).strftime(
            "%Y-%m-%d %H:%M UTC"
        )
