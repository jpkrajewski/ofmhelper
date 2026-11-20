from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ofmhelpers.db.cookies.schema import CookieSite
from ofmhelpers.db.tracked_accounts.schema import names_of, platform_names

_ICONIFY = "https://api.iconify.design"


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ServiceKind(StrEnum):
    EMAIL = "email"
    SOCIAL = "social"
    OTHER = "other"


class CookieLogin(_Frozen):
    domains: list[str] = Field(min_length=1)
    login: str


class LoginService(_Frozen):
    key: str
    label: str
    kind: ServiceKind
    icon: str = Field(pattern=r"^[a-z0-9-]+:[a-z0-9-]+$")
    icon_color: str | None = Field(default=None, pattern=r"^#[0-9a-f]{6}$")
    custom: bool = False
    tracked_platform: str | None = None
    profile_url: str | None = None
    cookies: CookieLogin | None = None

    @property
    def cookie_site(self) -> CookieSite | None:
        if self.cookies is None:
            return None
        return CookieSite(
            key=self.key,
            label=self.label,
            icon_url=self.icon_url,
            domains=self.cookies.domains,
            login=self.cookies.login,
        )

    def username(self, text: str) -> str:
        tracked = self.tracked_platform
        if tracked is not None and tracked in platform_names():
            return names_of(tracked).handle(text)
        name = text.strip()
        if self.kind is not ServiceKind.OTHER:
            name = name.lower()
        if self.kind is ServiceKind.SOCIAL:
            name = name.lstrip("@")
        if not name:
            msg = f"Enter the {self.label} login"
            raise ValueError(msg)
        return name

    def link(self, username: str) -> str | None:
        if self.profile_url is None:
            return None
        return self.profile_url.format(username=username)

    @property
    def icon_url(self) -> str:
        prefix, name = self.icon.split(":")
        url = f"{_ICONIFY}/{prefix}/{name}.svg"
        if self.icon_color:
            url += f"?color=%23{self.icon_color[1:]}"
        return url


class SizeClass(StrEnum):
    COMPACT = "compact"
    MINI = "mini"
    STANDARD = "standard"
    MAX = "max"


class CameraLayout(StrEnum):
    SINGLE_BARE = "single_bare"
    SINGLE_BUMP = "single_bump"
    PILL_X = "pill_x"
    DUAL_SQUARE = "dual_square"
    DUAL_DIAGONAL = "dual_diagonal"
    DUAL_PILL = "dual_pill"
    TRIPLE = "triple"
    PLATEAU_TRIPLE = "plateau_triple"
    PLATEAU_SINGLE = "plateau_single"


class Finish(_Frozen):
    id: str
    label: str
    hex: str = Field(pattern=r"^#[0-9a-f]{6}$")


class PhoneModel(_Frozen):
    id: str
    label: str
    year: int
    size: SizeClass
    camera: CameraLayout
    lidar: bool = False
    palette: str


class Family(_Frozen):
    id: str
    label: str
    models: list[PhoneModel] = Field(min_length=1)


class Catalog(_Frozen):
    palettes: dict[str, list[Finish]]
    families: list[Family]

    @model_validator(mode="after")
    def _consistent(self) -> Catalog:
        ids = [m.id for f in self.families for m in f.models]
        if len(ids) != len(set(ids)):
            msg = "duplicate model id in catalog.json"
            raise ValueError(msg)
        for f in self.families:
            for m in f.models:
                if m.palette not in self.palettes:
                    msg = f"{m.id}: unknown palette {m.palette}"
                    raise ValueError(msg)
        return self

    def colors(self, model: PhoneModel) -> list[Finish]:
        return self.palettes[model.palette]

    def color(self, model: PhoneModel, color_id: str) -> Finish | None:
        return next((c for c in self.colors(model) if c.id == color_id), None)
