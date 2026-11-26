# Copyright (C) 2026 jpkrajewski

from functools import lru_cache
from types import MappingProxyType

from ofmhelpers.integrations.socials.platforms.base import Platform
from ofmhelpers.integrations.socials.platforms.instagram import InstagramPlatform
from ofmhelpers.integrations.socials.platforms.snapchat import SnapchatPlatform
from ofmhelpers.integrations.socials.platforms.x import XPlatform

_PLATFORM_TYPES: tuple[type[Platform], ...] = (
    InstagramPlatform,
    SnapchatPlatform,
    XPlatform,
)


@lru_cache(maxsize=1)
def platforms() -> MappingProxyType[str, Platform]:
    return MappingProxyType({cls.key: cls() for cls in _PLATFORM_TYPES})


def get_platform(key: str) -> Platform:
    return platforms()[key]


__all__ = ["Platform", "get_platform", "platforms"]
