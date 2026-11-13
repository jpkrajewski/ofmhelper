"""What /phones knows that is not user data, shipped as JSON beside this
file and validated on first use:

- `services.json` -- the login services (iCloud, Gmail, ..., Other).
- `catalog.json` -- every iPhone model with its official colors and storage.

Adding a service, a model or a color is one JSON entry; the forms, cards,
filters, export and the phone drawings (`render.py`) all read these
accessors."""

from functools import lru_cache
from importlib import resources

from pydantic import BaseModel, TypeAdapter

from ofmhelpers.schemas.phone_catalog.models import (
    CameraLayout,
    Catalog,
    Family,
    Finish,
    LoginService,
    PhoneModel,
    ServiceKind,
    SizeClass,
)
from ofmhelpers.utils.registry import Registry
lazy from ofmhelpers.schemas.cookies import CookieSite

__all__ = [
    "CameraLayout",
    "Catalog",
    "Family",
    "Finish",
    "ListedModel",
    "LoginService",
    "PhoneModel",
    "ServiceKind",
    "SizeClass",
    "catalog",
    "cookie_sites",
    "find_model",
    "get_service",
    "services",
    "tracked_services",
]


class ListedModel(BaseModel, frozen=True):
    """A catalog model with the family it is listed under."""

    family: Family
    model: PhoneModel


def _read(name: str) -> str:
    return resources.files(__package__).joinpath(name).read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def services() -> Registry[LoginService]:
    """Every login service by its stored key, in display order."""
    listed = TypeAdapter(list[LoginService]).validate_json(_read("services.json"))
    return Registry[LoginService](entries={s.key: s for s in listed})


@lru_cache(maxsize=1)
def tracked_services() -> Registry[LoginService]:
    """The login services whose accounts /accounts tracks, by that platform
    ("instagram" -> the Instagram service)."""
    return Registry[LoginService](
        entries={s.tracked_platform: s for s in services().all() if s.tracked_platform}
    )


def cookie_sites() -> list[CookieSite]:
    """The services whose login a cookies.txt may carry (`cookies` in
    services.json), in display order: what /cookies checks."""
    return [s.cookie_site for s in services().all() if s.cookie_site]


def get_service(key: str) -> LoginService:
    """Raises ValueError for a service not (or no longer) listed."""
    try:
        return services()[key]
    except KeyError:
        msg = f"Unknown login service: {key}"
        raise ValueError(msg) from None


@lru_cache(maxsize=1)
def catalog() -> Catalog:
    return Catalog.model_validate_json(_read("catalog.json"))


@lru_cache(maxsize=1)
def _models() -> Registry[ListedModel]:
    return Registry[ListedModel](
        entries={
            m.id: ListedModel(family=f, model=m)
            for f in catalog().families
            for m in f.models
        }
    )


def find_model(model_id: str) -> ListedModel | None:
    """The model and its family; None for an id not (or no longer) listed."""
    return _models().get(model_id)
