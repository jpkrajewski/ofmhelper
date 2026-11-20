from types import MappingProxyType

from ofmhelpers.config import settings
from ofmhelpers.integrations.ai_providers.schema import KeySlot

WAN = "wan"
SEEDANCE = "seedance"
DEFAULT = "default"
GROUP_OF_PREFIX: tuple[tuple[str, str], ...] = (
    ("wan", WAN),
    ("happyhorse", WAN),
    ("doubao-seedance", SEEDANCE),
    ("seedance", SEEDANCE),
)


def group_of(model_id: str) -> str:
    name = model_id.lower()
    return next(
        (g for prefix, g in GROUP_OF_PREFIX if name.startswith(prefix)), DEFAULT
    )


def _slot(group: str) -> str:
    return "apiyi" if group == DEFAULT else f"apiyi-{group}"


def slot_of(model_id: str) -> str:
    return _slot(group_of(model_id))


def slot_listing(model_ids: list[str]) -> str | None:
    groups = {group_of(model_id) for model_id in model_ids}
    found = next((g for g in (DEFAULT, WAN, SEEDANCE) if g in groups), None)
    return None if found is None else _slot(found)


SLOTS = (
    KeySlot(id="apiyi", label="APIYI"),
    KeySlot(id=f"apiyi-{WAN}", label="APIYI Wan & HappyHorse"),
    KeySlot(id=f"apiyi-{SEEDANCE}", label="APIYI Seedance"),
)


def admin_keys() -> MappingProxyType[str, str]:
    s = settings.apiyi
    keys = {
        WAN: s.api_key_admin,
        SEEDANCE: s.api_key_seedance,
        DEFAULT: s.api_key_default,
    }
    return MappingProxyType({group: key for group, key in keys.items() if key})


def key_for(model_id: str, given: str) -> str:
    keys = admin_keys()
    if given not in keys.values():
        return given
    return keys.get(group_of(model_id)) or given
