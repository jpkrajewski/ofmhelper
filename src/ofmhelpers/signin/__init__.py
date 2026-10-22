"""How people sign in: Google and Apple (OpenID Connect, one module each,
behind `base.Provider`) and email + password (`passwords`). A new provider
is one module and one entry in `providers()`."""

from functools import lru_cache

from ofmhelpers.signin.apple import Apple
from ofmhelpers.signin.base import Identity, Provider, SignInError, TokenReply
from ofmhelpers.signin.google import Google


@lru_cache(maxsize=1)
def providers() -> dict[str, Provider]:
    """Every provider by name, in the order the sign-in page shows them."""
    found: list[Provider] = [Google(), Apple()]
    return {p.name: p for p in found}


def configured() -> list[Provider]:
    """The providers whose credentials are set: the buttons to show."""
    return [p for p in providers().values() if p.configured()]


__all__ = [
    "Identity",
    "Provider",
    "SignInError",
    "TokenReply",
    "configured",
    "providers",
]
