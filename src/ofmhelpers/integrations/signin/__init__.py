# Copyright (C) 2026 jpkrajewski

from functools import lru_cache
from types import MappingProxyType

from ofmhelpers.db.users.schema import Identity
from ofmhelpers.integrations.signin.apple import Apple
from ofmhelpers.integrations.signin.base import (
    Provider,
    SignInError,
    TokenReply,
)
from ofmhelpers.integrations.signin.google import Google
from ofmhelpers.integrations.signin.owner import Owner


@lru_cache(maxsize=1)
def providers() -> MappingProxyType[str, Provider]:
    found: list[Provider] = [Google(), Apple(), Owner()]
    return MappingProxyType({p.name: p for p in found})


def configured() -> list[Provider]:
    return [p for p in providers().values() if p.configured()]


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
