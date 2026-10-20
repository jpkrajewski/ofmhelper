"""Every platform /accounts can track. Adding one = its file beside this one
and its class in `_PLATFORM_TYPES`; the list page, the add dialog, the
filters and the tracker all read `platforms()`."""

from functools import lru_cache

from ofmhelpers.socials.platforms.base import Platform
from ofmhelpers.socials.platforms.instagram import InstagramPlatform

# Display order of the platform pickers.
_PLATFORM_TYPES: tuple[type[Platform], ...] = (InstagramPlatform,)


@lru_cache(maxsize=1)
def platforms() -> dict[str, Platform]:
    """Every registered platform by its stored key."""
    return {cls.key: cls() for cls in _PLATFORM_TYPES}


def get_platform(key: str) -> Platform:
    """Raises KeyError for a platform not (or no longer) registered."""
    return platforms()[key]


__all__ = ["Platform", "get_platform", "platforms"]
