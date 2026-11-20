from functools import lru_cache

from ofmhelpers.integrations.senders.base import Sender, SendError
from ofmhelpers.integrations.senders.telegram import TelegramSender
from ofmhelpers.utils.registry import Registry


@lru_cache(maxsize=1)
def senders() -> Registry[Sender]:
    found: list[Sender] = [TelegramSender()]
    return Registry[Sender](entries={s.name: s for s in found})


__all__ = ["SendError", "Sender", "senders"]
