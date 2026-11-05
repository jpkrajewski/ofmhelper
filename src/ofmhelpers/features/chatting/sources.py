"""Every chat platform with an API here. A new platform = one line in `_all`."""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING

from ofmhelpers.features.chatting.fanvue_tokens import FanvueTokens
from ofmhelpers.integrations.fanvue import FanvueSource
from ofmhelpers.integrations.stacked.source import StackedSource
from ofmhelpers.schemas.chatting import ChatPlatform

if TYPE_CHECKING:
    from ofmhelpers.integrations.chat_source import ChatSource


@lru_cache(maxsize=1)
def _all() -> dict[ChatPlatform, ChatSource]:
    return {
        ChatPlatform.STACKED: StackedSource(),
        ChatPlatform.FANVUE: FanvueSource(FanvueTokens()),
    }


def platforms() -> list[ChatPlatform]:
    """Those with an API here, configured or not."""
    return list(_all())


def source_for(platform: ChatPlatform) -> ChatSource | None:
    return _all().get(platform)


def configured_sources() -> list[ChatSource]:
    return [source for source in _all().values() if source.configured()]
