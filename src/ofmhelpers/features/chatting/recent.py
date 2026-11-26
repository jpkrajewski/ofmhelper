# Copyright (C) 2026 jpkrajewski

import time
from typing import Any

from redis.exceptions import RedisError

from ofmhelpers.cache import get_redis
from ofmhelpers.config import settings
from ofmhelpers.db.chats.schema import ChatPlatform, RecentModel
from ofmhelpers.log import get_logger

logger = get_logger(__name__)

_KEY_PREFIX = "chatting:recent:"


def _key(user_id: int) -> str:
    return f"{_KEY_PREFIX}{user_id}"


def remember(user_id: int, platform: ChatPlatform, username: str) -> None:
    key = _key(user_id)
    try:
        redis = get_redis()
        redis.zadd(key, {f"{platform.value}/{username}": time.time()})
        redis.zremrangebyrank(key, 0, -(settings.chatting.recent_models_shown + 1))
    except RedisError:
        logger.warning("could not remember a recent model", exc_info=True)


def recent_models(user_id: int) -> list[RecentModel]:
    try:
        members: Any = get_redis().zrevrange(
            _key(user_id),
            0,
            settings.chatting.recent_models_shown - 1,
        )
    except RedisError:
        logger.warning("could not read recent models", exc_info=True)
        return []
    found = []
    for raw in members:
        member = raw.decode() if isinstance(raw, bytes) else str(raw)
        platform, _, username = member.partition("/")
        found.append(RecentModel(platform=ChatPlatform(platform), username=username))
    return found
