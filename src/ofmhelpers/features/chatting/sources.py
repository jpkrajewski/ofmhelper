# Copyright (C) 2026 jpkrajewski

from functools import lru_cache

from ofmhelpers.features.account.stacked_keys import StackedKeys
from ofmhelpers.features.chatting.fanvue_tokens import FanvueTokens
from ofmhelpers.integrations.fanvue import FanvueSource
from ofmhelpers.integrations.stacked.source import StackedSource
lazy from ofmhelpers.db.chats.schema import ChatPlatform
lazy from ofmhelpers.integrations.chat_source import ChatSource


@lru_cache(maxsize=1)
def _all() -> list[ChatSource]:
    return [
        StackedSource(StackedKeys()),
        FanvueSource(FanvueTokens()),
    ]


def platforms() -> list[ChatPlatform]:
    return [source.platform for source in _all()]


def source_for(platform: ChatPlatform) -> ChatSource | None:
    return next((source for source in _all() if source.platform == platform), None)


def configured_sources() -> list[ChatSource]:
    return [source for source in _all() if source.configured()]
