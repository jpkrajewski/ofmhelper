from types import MappingProxyType

from ofmhelpers.integrations.short_links.providers.base import LinkProvider
from ofmhelpers.integrations.short_links.providers.bouncy import BouncyProvider
from ofmhelpers.integrations.short_links.providers.oopsie import OopsieProvider
from ofmhelpers.integrations.short_links.providers.self_host import SelfProvider

_PROVIDER_TYPES: tuple[type[LinkProvider], ...] = (
    SelfProvider,
    OopsieProvider,
    BouncyProvider,
)


def link_providers() -> MappingProxyType[str, type[LinkProvider]]:
    return MappingProxyType({cls.key: cls for cls in _PROVIDER_TYPES})


def get_link_provider(key: str) -> type[LinkProvider]:
    return link_providers()[key]


__all__ = ["LinkProvider", "get_link_provider", "link_providers"]
