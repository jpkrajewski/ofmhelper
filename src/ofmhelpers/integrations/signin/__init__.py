"""How people sign in: Google and Apple (OpenID Connect, one module each,
behind `base.Provider`), the app owner (the admin password, `owner`) and
email + password (`passwords`). A new provider is one module and one entry
in `providers()`."""

from functools import lru_cache

from ofmhelpers.integrations.signin.apple import Apple
from ofmhelpers.integrations.signin.base import (
    Provider,
    SignInError,
    TokenReply,
)
from ofmhelpers.integrations.signin.google import Google
from ofmhelpers.integrations.signin.owner import Owner
from ofmhelpers.schemas.users import Identity
from ofmhelpers.utils.registry import Registry


@lru_cache(maxsize=1)
def providers() -> Registry[Provider]:
    """Every provider by name, in the order the sign-in page shows them."""
    found: list[Provider] = [Google(), Apple(), Owner()]
    return Registry[Provider](entries={p.name: p for p in found})


def configured() -> list[Provider]:
    """The providers whose credentials are set: the buttons to show."""
    return [p for p in providers().all() if p.configured()]


def linkable() -> list[Provider]:
    """The configured providers a person can link to their account
    (/account): their own Google or Apple, not the app owner."""
    return [p for p in configured() if p.linkable]


__all__ = [
    "Identity",
    "Provider",
    "SignInError",
    "TokenReply",
    "configured",
    "linkable",
    "providers",
]
