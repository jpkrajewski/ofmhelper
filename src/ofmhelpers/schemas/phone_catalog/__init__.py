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
    family: Family
    model: PhoneModel


def _read(name: str) -> str:
    return resources.files(__package__).joinpath(name).read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def services() -> Registry[LoginService]:
    listed = TypeAdapter(list[LoginService]).validate_json(_read("services.json"))
    return Registry[LoginService](entries={s.key: s for s in listed})


@lru_cache(maxsize=1)
def tracked_services() -> Registry[LoginService]:
    return Registry[LoginService](
        entries={s.tracked_platform: s for s in services().all() if s.tracked_platform}
    )


def cookie_sites() -> list[CookieSite]:
    return [s.cookie_site for s in services().all() if s.cookie_site]


def get_service(key: str) -> LoginService:
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
    return _models().get(model_id)
