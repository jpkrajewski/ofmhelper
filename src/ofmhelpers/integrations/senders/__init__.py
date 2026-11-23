# Copyright (C) 2026 jpkrajewski

from functools import lru_cache
from types import MappingProxyType

from ofmhelpers.integrations.senders.base import Sender, SendError
from ofmhelpers.integrations.senders.telegram import TelegramSender


@lru_cache(maxsize=1)
def senders() -> MappingProxyType[str, Sender]:
    found: list[Sender] = [TelegramSender()]
    return MappingProxyType({s.name: s for s in found})


__all__ = ["SendError", "Sender", "senders"]
