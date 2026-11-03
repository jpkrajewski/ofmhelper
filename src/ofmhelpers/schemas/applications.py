"""A landing-page application: what the public form posts (`ApplicationForm`,
field names are contract with templates/reachmodel.html) and the stored lead
(`Application`, what the admin CRM page lists)."""

import re
from datetime import UTC, datetime

from pydantic import BaseModel, computed_field, field_validator, model_validator


class ApplicationFields(BaseModel):
    """The fields of one application, with no rules attached: shared by the
    form (which enforces them on submit) and the stored lead (which must load
    whatever was accepted at the time, even under rules added since)."""

    first_name: str
    last_name: str
    email: str
    phone: str = ""
    telegram_handle: str = ""
    instagram_handle: str = ""
    monthly_revenue: str = ""
    experience_level: str = ""
    goals: str = ""


# What a real lead never types, and every spam bot so far did (prod,
# 2026-10-22: BBCode [url=...] links, "telegram" handles of two random
# words, the same message in a dozen languages).
_LINK = re.compile(r"https?://|www\.|\[/?url|<a\s|\[/?link", re.IGNORECASE)
# Telegram: 5-32 letters, digits, underscores, starting with a letter.
_TELEGRAM = re.compile(r"@?[A-Za-z][A-Za-z0-9_]{4,31}")
# Instagram: up to 30 letters, digits, dots, underscores.
_INSTAGRAM = re.compile(r"@?[A-Za-z0-9._]{1,30}")
# A phone number: digits with the usual separators, 7-20 digits.
_PHONE = re.compile(r"\+?[\d\s().-]{7,25}")
_MAX_GOALS = 2000
_MAX_NAME = 60
_MAX_PHONE_DIGITS = 20


class ApplicationForm(ApplicationFields):
    """What the landing page posts. The two `<select>`s and the Instagram
    handle are optional: their placeholder option posts an empty string, and a
    lead who skipped a dropdown is still a lead.

    `phone` and `telegram_handle` are individually optional but not jointly:
    the form asks for one direct channel, and the browser cannot express
    "either of these two", so the rule is enforced here."""

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
        # Deliberately not a full RFC check (that needs email-validator, a dep
        # this app doesn't carry): the point is to reject an empty or obviously
        # junk box, not to prove the address is deliverable.
        local, _, domain = v.partition("@")
        if not local or "." not in domain:
            msg = "must be an email address"
            raise ValueError(msg)
        return v

    @model_validator(mode="after")
    def _one_direct_channel(self) -> "ApplicationForm":
        if not self.phone and not self.telegram_handle:
            msg = "give a phone number or a Telegram handle"
            raise ValueError(msg)
        return self

    def spam_reason(self) -> str | None:
        """Why this looks like a bot, not a lead; None when it looks real.
        The landing form checks the same shapes in the browser, so a person
        is told what to fix; only a bot, which posts past the browser, gets
        here with them wrong."""
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
    """A stored lead: the form's fields plus its id and when it came in. No
    form rules here -- a lead accepted before a rule existed (e.g. one with
    neither phone nor Telegram) must still list."""

    id: str
    created_at: float

    @computed_field  # type: ignore[prop-decorator]
    @property
    def created_at_display(self) -> str:
        return datetime.fromtimestamp(self.created_at, UTC).strftime(
            "%Y-%m-%d %H:%M UTC"
        )
