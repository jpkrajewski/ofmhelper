"""The shapes /phones reads from the package's JSON: login services
(`services.json`) and the iPhone catalog (`catalog.json`)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ofmhelpers.downloaders.models import CookieSite
from ofmhelpers.socials import get_platform, platforms

_ICONIFY = "https://api.iconify.design"


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# --- login services -----------------------------------------------------------


class ServiceKind(StrEnum):
    # An account the phone signs in with (iCloud, Gmail): the login is an email.
    EMAIL = "email"
    # A social account posted from the phone: the login is a handle.
    SOCIAL = "social"
    # Anything else (a website, an app): the login is kept as typed.
    OTHER = "other"


class CookieLogin(_Frozen):
    """Where a service's cookies live and which one means logged in."""

    domains: list[str] = Field(min_length=1)
    login: str


class LoginService(_Frozen):
    # Stored on every login; never rename one in use.
    key: str
    label: str
    kind: ServiceKind
    # The logo, an Iconify icon ("set:name", https://icon-sets.iconify.design),
    # hotlinked from its API: full-color brand logos (`logos:`) where they
    # exist, a one-color icon tinted `icon_color` otherwise.
    icon: str = Field(pattern=r"^[a-z0-9-]+:[a-z0-9-]+$")
    icon_color: str | None = Field(default=None, pattern=r"^#[0-9a-f]{6}$")
    # The login names its provider ("Acme Corp", "Shopify"): the form asks
    # for it and it is part of the login's identity.
    custom: bool = False
    # The `socials.platforms()` key /accounts tracks this service's
    # accounts under, if it does: the login links to its analytics.
    tracked_platform: str | None = None
    # `{username}` is replaced; None: no profile page to link.
    profile_url: str | None = None
    # The cookies that log into it (/cookies shows whether a cookies.txt
    # does); None: not a site cookies matter for.
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
        """`text` as typed reduced to the stored username: a tracked
        platform's own handle rules (so "@Name" and a profile URL both
        match its /accounts row); an email or handle trimmed and lowercase,
        a handle without its "@"; anything else only trimmed. Raises
        ValueError when nothing is left."""
        if self.tracked_platform in platforms():
            return get_platform(self.tracked_platform).handle(text)
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


# --- iPhone catalog -----------------------------------------------------------


class SizeClass(StrEnum):
    """How big the phone is next to the others (its drawing's size): SE,
    mini, the 6.1" models, Plus / Max / Air."""

    COMPACT = "compact"
    MINI = "mini"
    STANDARD = "standard"
    MAX = "max"


class CameraLayout(StrEnum):
    """What the back's camera looks like -- the part that tells models
    apart at a glance (phones/render.py draws each)."""

    # One lens, no bump (SE, 16e, 17e).
    SINGLE_BARE = "single_bare"
    # One lens in a square bump (XR).
    SINGLE_BUMP = "single_bump"
    # Two lenses in a vertical pill, flash inside it (X, XS).
    PILL_X = "pill_x"
    # Two lenses stacked in a square bump (11, 12, 12 mini).
    DUAL_SQUARE = "dual_square"
    # Two lenses on the diagonal of a square bump (13, 14, 15).
    DUAL_DIAGONAL = "dual_diagonal"
    # Two lenses in a vertical pill, flash beside it (16, 17).
    DUAL_PILL = "dual_pill"
    # Three lenses in a triangle in a square bump (11 Pro to 16 Pro).
    TRIPLE = "triple"
    # Three lenses at the left of a full-width plateau (17 Pro, 18 Pro).
    PLATEAU_TRIPLE = "plateau_triple"
    # One lens in a full-width bar (Air).
    PLATEAU_SINGLE = "plateau_single"


class Finish(_Frozen):
    """One official color of a model."""

    id: str
    label: str
    # The finish as a flat color: the swatch and the drawing.
    hex: str = Field(pattern=r"^#[0-9a-f]{6}$")


class PhoneModel(_Frozen):
    id: str
    label: str
    year: int
    size: SizeClass
    camera: CameraLayout
    # The LiDAR dot beside the lenses (Pro models from the 12 Pro on).
    lidar: bool = False
    palette: str


class Family(_Frozen):
    id: str
    label: str
    models: list[PhoneModel] = Field(min_length=1)


class Catalog(_Frozen):
    """Families newest first; a model's colors are its palette's, shared by
    models sold in the same colors (iPhone 13 and 13 mini)."""

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
