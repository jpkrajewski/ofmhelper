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
    found: list[Provider] = [Google(), Apple(), Owner()]
    return Registry[Provider](entries={p.name: p for p in found})


def configured() -> list[Provider]:
    return [p for p in providers().all() if p.configured()]


def linkable() -> list[Provider]:
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
