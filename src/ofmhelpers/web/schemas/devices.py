"""The agency's phones (/phones) and their logins: the forms' bodies, what
the page lists (no secret in it), the filters / sort, and the one login's
secrets the page asks for on Show / Copy."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import (
    BaseModel,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from ofmhelpers.phones import (
    Family,
    Finish,
    LoginService,
    PhoneModel,
    catalog,
    find_model,
    get_service,
    services,
)

_Text = Annotated[str, StringConstraints(max_length=5000)]
_Label = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=32)
]
_Name = Annotated[str, StringConstraints(strip_whitespace=True, max_length=64)]
_Number = Annotated[str, StringConstraints(strip_whitespace=True, max_length=32)]


def drawing_url(model_id: str | None, color_id: str | None) -> str:
    """Where the phone's drawing is served; the browser caches one per
    model and color."""
    return f"/phones/drawing.svg?model={model_id or ''}&color={color_id or ''}"


class DeviceFields(BaseModel):
    """The add / edit phone dialog: a catalog model and one of its official
    colors -- or, for a phone the catalog does not list, `custom_model` and
    no ids."""

    label: _Label
    model_id: str | None = None
    color_id: str | None = None
    custom_model: _Name = ""
    owner: _Name = ""
    phone_number: _Number = ""
    notes: _Text = ""

    @field_validator("model_id", "color_id", mode="before")
    @classmethod
    def _blank_is_none(cls, value: object) -> object:
        return value or None

    @model_validator(mode="after")
    def _in_catalog(self) -> DeviceFields:
        if self.model_id is None:
            if self.color_id:
                msg = "Pick a model before its color"
                raise ValueError(msg)
            return self
        found = find_model(self.model_id)
        if found is None:
            msg = f"Unknown model: {self.model_id}"
            raise ValueError(msg)
        model = found[1]
        if self.color_id and catalog().color(model, self.color_id) is None:
            msg = f"{model.label} never came in {self.color_id}"
            raise ValueError(msg)
        self.custom_model = ""
        return self


class LoginFields(BaseModel):
    """The add / edit login dialog. `username` is stored the way the
    service writes it (`LoginService.username`); `provider` names a custom
    service's site ("" for the others); the password is kept exactly as
    typed, spaces included."""

    service: str
    provider: _Name = ""
    username: Annotated[str, StringConstraints(min_length=1, max_length=255)]
    password: Annotated[str, StringConstraints(max_length=1000)] = ""
    backup_codes: _Text = ""
    notes: _Text = ""

    @model_validator(mode="after")
    def _normalised(self) -> LoginFields:
        service = get_service(self.service)
        if service.custom and not self.provider:
            msg = f"Name the {service.label.lower()} (e.g. the company or site)"
            raise ValueError(msg)
        if not service.custom:
            self.provider = ""
        self.username = service.username(self.username)
        self.backup_codes = self.backup_codes.strip()
        return self


class LoginSecrets(BaseModel):
    """What Show / Copy / Edit fetch for one login, never in the page."""

    password: str
    backup_codes: str


class DeviceLogin(BaseModel):
    id: int
    service: str
    provider: str
    username: str
    notes: str
    has_password: bool
    has_codes: bool
    updated_at: float
    # Every phone it is signed in on (labels), this one included.
    on: list[str] = Field(default_factory=list)
    # Only in the export.
    password: str = ""
    backup_codes: str = ""
    # Its /accounts row, when that platform tracks this handle, and that
    # row's latest follower count (None until its first scan).
    tracked_account_id: int | None = None
    tracked_followers: int | None = None

    @property
    def followers_label(self) -> str:
        """ "12.3k followers"; "" when not tracked or not scanned yet."""
        n = self.tracked_followers
        if n is None:
            return ""
        return f"{_compact(n)} follower{'' if n == 1 else 's'}"

    @property
    def info(self) -> LoginService | None:
        """None for a service no longer in services.json: still listed
        and exported, under its stored key."""
        return services().get(self.service)

    @property
    def label(self) -> str:
        if self.provider:
            return self.provider
        return self.info.label if self.info else self.service

    @property
    def icon_url(self) -> str | None:
        return self.info.icon_url if self.info else None

    @property
    def link(self) -> str | None:
        return self.info.link(self.username) if self.info else None

    def elsewhere(self, label: str) -> list[str]:
        """The other phones it is on."""
        return [p for p in self.on if p != label]


_THOUSAND = 1_000
_MILLION = 1_000_000


def _compact(n: int) -> str:
    """950, 12.3k, 1.2M: one decimal where it says something. The unit is
    picked after rounding (999_960 is "1M", not "1000k")."""
    if n < _THOUSAND:
        return str(n)
    thousands = round(n / _THOUSAND, 1)
    if thousands < _THOUSAND:
        return f"{thousands:.1f}".rstrip("0").rstrip(".") + "k"
    return f"{round(n / _MILLION, 1):.1f}".rstrip("0").rstrip(".") + "M"


class Device(BaseModel):
    id: int
    # Whose phone it is (a user; `owner` is the person who holds it).
    user_id: int
    label: str
    model_id: str | None
    color_id: str | None
    custom_model: str
    owner: str
    phone_number: str
    notes: str
    added_at: float
    updated_at: float
    retired_at: float | None
    logins: list[DeviceLogin] = Field(default_factory=list)

    @property
    def retired(self) -> bool:
        return self.retired_at is not None

    @property
    def followers(self) -> int | None:
        """The most followers of any tracked account signed in on it."""
        counts = [
            x.tracked_followers for x in self.logins if x.tracked_followers is not None
        ]
        return max(counts) if counts else None

    @property
    def family(self) -> Family | None:
        found = find_model(self.model_id) if self.model_id else None
        return found[0] if found else None

    @property
    def model(self) -> PhoneModel | None:
        found = find_model(self.model_id) if self.model_id else None
        return found[1] if found else None

    @property
    def finish(self) -> Finish | None:
        model = self.model
        if model is None or not self.color_id:
            return None
        return catalog().color(model, self.color_id)

    @property
    def model_label(self) -> str:
        if self.model:
            return self.model.label
        return self.custom_model or "Unknown model"

    @property
    def specs(self) -> str:
        """Model and color, whichever are known: "iPhone 13 · Blue"."""
        finish = self.finish.label if self.finish else ""
        return " · ".join(p for p in (self.model_label, finish) if p)

    @property
    def drawing_url(self) -> str:
        """Its back, drawn from the catalog (routers/inventory/phones.py
        serves `ofmhelpers.phones.render`)."""
        return drawing_url(self.model_id, self.color_id)

    @property
    def codes_count(self) -> int:
        return sum(1 for login in self.logins if login.has_codes)

    @property
    def search_text(self) -> str:
        """What the search box matches: the phone, its model, and every
        login's name and provider -- never a password."""
        parts = [
            self.label,
            self.specs,
            self.owner,
            self.phone_number,
            *(f"{x.label} {x.username}" for x in self.logins),
        ]
        return " ".join(parts).lower()


class PhoneSort(StrEnum):
    FOLLOWERS = "followers"
    LABEL = "label"
    MODEL = "model"
    UPDATED = "updated"
    LOGINS = "logins"


class PhoneQuery(BaseModel):
    """The toolbar's filters and sort, as the query string (blank = unset),
    so every view is a link. Search is instant, in the page."""

    family: str = ""
    color: str = ""
    owner: str = ""
    service: str = ""
    sort: PhoneSort = PhoneSort.FOLLOWERS

    @field_validator("sort", mode="before")
    @classmethod
    def _blank_sort(cls, value: object) -> object:
        return value or PhoneSort.FOLLOWERS

    @property
    def filtered(self) -> bool:
        return bool(self.family or self.color or self.owner or self.service)

    def matches(self, d: Device) -> bool:
        family = d.family.id if d.family else ""
        color = d.finish.label if d.finish else ""
        return (
            (not self.family or family == self.family)
            and (not self.color or color == self.color)
            and (not self.owner or d.owner == self.owner)
            and (not self.service or any(x.service == self.service for x in d.logins))
        )

    def ordered(self, devices: list[Device]) -> list[Device]:
        if self.sort is PhoneSort.MODEL:
            return sorted(
                devices,
                key=lambda d: (
                    -(d.model.year if d.model else 0),
                    d.model_label,
                    d.label,
                ),
            )
        if self.sort is PhoneSort.UPDATED:
            return sorted(devices, key=lambda d: -d.updated_at)
        if self.sort is PhoneSort.LOGINS:
            return sorted(devices, key=lambda d: (-len(d.logins), d.label.lower()))
        if self.sort is PhoneSort.FOLLOWERS:
            # Most followed first; phones with no tracked account last.
            return sorted(
                devices,
                key=lambda d: (
                    d.followers is None,
                    -(d.followers or 0),
                    d.label.lower(),
                ),
            )
        return sorted(devices, key=lambda d: d.label.lower())
