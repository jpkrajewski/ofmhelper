from functools import lru_cache
from importlib import resources
from types import MappingProxyType

from pydantic import TypeAdapter

from ofmhelpers.db.short_links.catalog.schema import (
    DayStat,
    HourStat,
    LinkDays,
    LinkHours,
    NewLink,
    ProviderInfo,
    RemoteLink,
    SlugRules,
    Target,
    Usage,
)

__all__ = [
    "DayStat",
    "HourStat",
    "LinkDays",
    "LinkHours",
    "NewLink",
    "ProviderInfo",
    "RemoteLink",
    "SlugRules",
    "Target",
    "Usage",
    "detect_target",
    "provider_infos",
    "targets",
]


def _read(name: str) -> str:
    return resources.files(__package__).joinpath(name).read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def provider_infos() -> MappingProxyType[str, ProviderInfo]:
    found = TypeAdapter(dict[str, ProviderInfo]).validate_json(_read("providers.json"))
    return MappingProxyType(
        {key: info.model_copy(update={"key": key}) for key, info in found.items()}
    )


@lru_cache(maxsize=1)
def targets() -> MappingProxyType[str, Target]:
    found = TypeAdapter(dict[str, Target]).validate_json(_read("targets.json"))
    return MappingProxyType(
        {key: t.model_copy(update={"key": key}) for key, t in found.items()}
    )


def detect_target(url: str) -> str | None:
    return next((t.key for t in targets().values() if t.matches(url)), None)
