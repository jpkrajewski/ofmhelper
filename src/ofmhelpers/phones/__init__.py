"""What /phones knows that is not user data, shipped as JSON beside this
file and validated on first use:

- `services.json` -- the login services (iCloud, Gmail, ..., Other).
- `catalog.json` -- every iPhone model with its official colors and storage.

Adding a service, a model or a color is one JSON entry; the forms, cards,
filters, export and the phone drawings (`render.py`) all read these
accessors."""

from functools import lru_cache
from importlib import resources

from pydantic import TypeAdapter

from ofmhelpers.phones.models import (
    CameraLayout,
    Catalog,
    Family,
    Finish,
    LoginService,
    PhoneModel,
    ServiceKind,
    SizeClass,
)

__all__ = [
    "CameraLayout",
    "Catalog",
    "Family",
    "Finish",
    "LoginService",
    "PhoneModel",
    "ServiceKind",
    "SizeClass",
    "catalog",
    "find_model",
    "get_service",
    "services",
]


def _read(name: str) -> str:
    return resources.files(__package__).joinpath(name).read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def services() -> dict[str, LoginService]:
    """Every login service by its stored key, in display order."""
    listed = TypeAdapter(list[LoginService]).validate_json(_read("services.json"))
    return {s.key: s for s in listed}


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
def _models() -> dict[str, tuple[Family, PhoneModel]]:
    return {m.id: (f, m) for f in catalog().families for m in f.models}


def find_model(model_id: str) -> tuple[Family, PhoneModel] | None:
    """The model and its family; None for an id not (or no longer) listed."""
    return _models().get(model_id)
