"""Short links on link providers (Oopsie, Bouncy): what /links makes, lists
and counts. The static data (`providers.json`, `targets.json`) and the
shapes every provider client returns are `ofmhelpers.schemas.link_catalog`
(the stored links and the forms read them too), re-exported here.

A new provider is a `providers/<name>.py` client plus its JSON entry; a new
target is one JSON entry."""

from ofmhelpers.integrations.short_links.errors import (
    AuthError,
    LinkError,
    QuotaError,
    RateLimitedError,
    SlugTakenError,
    UngatedError,
)
from ofmhelpers.schemas.link_catalog import (
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
    detect_target,
    provider_infos,
    targets,
)

__all__ = [
    "AuthError",
    "DayStat",
    "HourStat",
    "LinkDays",
    "LinkError",
    "LinkHours",
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
