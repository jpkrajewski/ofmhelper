from functools import lru_cache

from ofmhelpers.integrations.socials.platforms.base import Platform
from ofmhelpers.integrations.socials.platforms.instagram import InstagramPlatform
from ofmhelpers.utils.registry import Registry

_PLATFORM_TYPES: tuple[type[Platform], ...] = (InstagramPlatform,)


@lru_cache(maxsize=1)
def platforms() -> Registry[Platform]:
    return Registry[Platform](entries={cls.key: cls() for cls in _PLATFORM_TYPES})


def get_platform(key: str) -> Platform:
    return platforms()[key]


__all__ = ["Platform", "get_platform", "platforms"]
