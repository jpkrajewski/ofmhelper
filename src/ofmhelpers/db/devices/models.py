from types import MappingProxyType
from typing import Annotated

from pydantic import (
    BaseModel,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from ofmhelpers.db.devices.catalog import (
    Family,
    Finish,
    LoginService,
    PhoneModel,
    catalog,
    find_model,
    get_service,
    login_username,
    services,
)
from ofmhelpers.db.devices.schema import PhoneSort, drawing_url, followers_text

_Text = Annotated[str, StringConstraints(max_length=5000)]
_Label = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=32)
]
_Name = Annotated[str, StringConstraints(strip_whitespace=True, max_length=64)]
_Number = Annotated[str, StringConstraints(strip_whitespace=True, max_length=32)]


class DeviceFields(BaseModel):
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
        model = found.model
        if self.color_id and catalog().color(model, self.color_id) is None:
            msg = f"{model.label} never came in {self.color_id}"
            raise ValueError(msg)
        self.custom_model = ""
        return self


class LoginFields(BaseModel):
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
        self.username = login_username(service, self.username)
        self.backup_codes = self.backup_codes.strip()
        return self


class DeviceLogin(BaseModel):
    id: int
    service: str
    provider: str
    username: str
    notes: str
    has_password: bool
    has_codes: bool
    updated_at: float
    on: list[str] = Field(default_factory=list)
    password: str = ""
    backup_codes: str = ""
    tracked_account_id: int | None = None
    tracked_followers: int | None = None

    @property
    def followers_label(self) -> str:
        return followers_text(self.tracked_followers)

    @property
    def info(self) -> LoginService | None:
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
        return [p for p in self.on if p != label]


class Device(BaseModel):
    id: int
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

    def accounts_of(self, service: str) -> MappingProxyType[str, DeviceLogin]:
        return MappingProxyType(
            {x.username: x for x in self.logins if x.service == service}
        )

    @property
    def followers(self) -> int | None:
        counts = [
            x.tracked_followers for x in self.logins if x.tracked_followers is not None
        ]
        return max(counts) if counts else None

    @property
    def family(self) -> Family | None:
        found = find_model(self.model_id) if self.model_id else None
        return found.family if found else None

    @property
    def model(self) -> PhoneModel | None:
        found = find_model(self.model_id) if self.model_id else None
        return found.model if found else None

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
        finish = self.finish.label if self.finish else ""
        return " · ".join(p for p in (self.model_label, finish) if p)

    @property
    def drawing_url(self) -> str:
        return drawing_url(self.model_id, self.color_id)

    @property
    def codes_count(self) -> int:
        return sum(1 for login in self.logins if login.has_codes)

    @property
    def search_text(self) -> str:
        parts = [
            self.label,
            self.specs,
            self.owner,
            self.phone_number,
            *(f"{x.label} {x.username}" for x in self.logins),
        ]
        return " ".join(parts).lower()


class AccountPhones(BaseModel):
    account_id: int
    phones: list[Device]


class PhonesByAccount(BaseModel):
    rows: list[AccountPhones] = Field(default_factory=list)

    def of(self, account_id: int) -> list[Device]:
        for row in self.rows:
            if row.account_id == account_id:
                return row.phones
        return []


class PhoneQuery(BaseModel):
    family: str = ""
    color: str = ""
    owner: str = ""
    service: str = ""
    sort: PhoneSort = PhoneSort.FOLLOWERS
    assign: int | None = None

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
            return sorted(
                devices,
                key=lambda d: (
                    d.followers is None,
                    -(d.followers or 0),
                    d.label.lower(),
                ),
            )
        return sorted(devices, key=lambda d: d.label.lower())
