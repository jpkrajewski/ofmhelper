"""Short links on link providers (Oopsie, Bouncy): what /links makes, lists
and counts. Static data ships as JSON beside this file, validated on first
use:

- `providers.json` -- each provider's API, domains, slug rules and limits.
- `targets.json` -- the pages a link may send visitors to (OnlyFans,
  Fanvue, Stacked).

A new provider is a `providers/<name>.py` client plus its JSON entry; a new
target is one JSON entry."""

from functools import lru_cache
from importlib import resources

from pydantic import TypeAdapter

from ofmhelpers.links.errors import (
    AuthError,
    LinkError,
    QuotaError,
    RateLimitedError,
    SlugTakenError,
    UngatedError,
)
from ofmhelpers.links.models import (
    DayStat,
    NewLink,
    ProviderInfo,
    RemoteLink,
    SlugRules,
    Target,
    Usage,
)

__all__ = [
    "AuthError",
    "DayStat",
    "LinkError",
    "NewLink",
    "ProviderInfo",
    "QuotaError",
    "RateLimitedError",
    "RemoteLink",
    "SlugRules",
    "SlugTakenError",
    "Target",
    "UngatedError",
    "Usage",
    "detect_target",
    "provider_infos",
    "targets",
]


def _read(name: str) -> str:
    return resources.files(__package__).joinpath(name).read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def provider_infos() -> dict[str, ProviderInfo]:
    """Every provider's static facts by key, in display order."""
    found = TypeAdapter(dict[str, ProviderInfo]).validate_json(_read("providers.json"))
    return {key: info.model_copy(update={"key": key}) for key, info in found.items()}


@lru_cache(maxsize=1)
def targets() -> dict[str, Target]:
    """Every target page by key, in display order."""
    found = TypeAdapter(dict[str, Target]).validate_json(_read("targets.json"))
    return {key: t.model_copy(update={"key": key}) for key, t in found.items()}


def detect_target(url: str) -> str | None:
    """The key of the target `url` is on; None for any other page."""
    return next((key for key, t in targets().items() if t.matches(url)), None)
