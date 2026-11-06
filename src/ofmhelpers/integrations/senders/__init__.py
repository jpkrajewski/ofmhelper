"""The services events go out through (features/events): one package each behind
`base.Sender`. A new sender is one package and one entry in `senders()`. See
CLAUDE.md."""

from functools import lru_cache

from ofmhelpers.integrations.senders.base import Sender, SendError
from ofmhelpers.integrations.senders.telegram import TelegramSender


@lru_cache(maxsize=1)
def senders() -> dict[str, Sender]:
    """Every sender by name."""
    found: list[Sender] = [TelegramSender()]
    return {s.name: s for s in found}


__all__ = ["SendError", "Sender", "senders"]
