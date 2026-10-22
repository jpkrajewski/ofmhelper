"""Every link provider /links can use. Adding one = its file beside this
one, its class in `_PROVIDER_TYPES` and its `providers.json` entry."""

from ofmhelpers.links.providers.base import LinkProvider
from ofmhelpers.links.providers.bouncy import BouncyProvider
from ofmhelpers.links.providers.oopsie import OopsieProvider

# Display order of the provider pickers.
_PROVIDER_TYPES: tuple[type[LinkProvider], ...] = (OopsieProvider, BouncyProvider)


def link_providers() -> dict[str, type[LinkProvider]]:
    """Every registered provider by its stored key."""
    return {cls.key: cls for cls in _PROVIDER_TYPES}


def get_link_provider(key: str) -> type[LinkProvider]:
    """Raises KeyError for a provider not (or no longer) registered."""
    return link_providers()[key]


__all__ = ["LinkProvider", "get_link_provider", "link_providers"]
