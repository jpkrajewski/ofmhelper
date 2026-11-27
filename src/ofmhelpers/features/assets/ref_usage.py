# Copyright (C) 2026 jpkrajewski

import logging
import time
from typing import Any
lazy from pathlib import Path

from pydantic import BaseModel
from redis.exceptions import RedisError

from ofmhelpers.cache import get_redis
from ofmhelpers.config import settings
from ofmhelpers.db.users.store import get_user

logger = logging.getLogger(__name__)

_KEY_PREFIX = "refs:used:"


def _key(user_id: int) -> str:
    user = get_user(user_id)
    owner = user.owner_id if user is not None and user.owner_id else user_id
    return f"{_KEY_PREFIX}{owner}"


def record_use(user_id: int, path: Path) -> None:
    key = _key(user_id)
    try:
        redis = get_redis()
        redis.zadd(key, {str(path): time.time()})
        redis.zremrangebyrank(key, 0, -(settings.web.ref_usage_max_tracked + 1))
    except RedisError:
        logger.warning("could not record use of %s", path, exc_info=True)


class UsedRef(BaseModel, frozen=True):
    path: str
    used_at: float


def recent(user_id: int, limit: int) -> list[UsedRef]:
    try:
        rows: Any = get_redis().zrevrange(_key(user_id), 0, limit - 1, withscores=True)
    except RedisError:
        logger.warning("could not read recently-used refs", exc_info=True)
        return []
    return [
        UsedRef(
            path=raw.decode() if isinstance(raw, bytes) else str(raw),
            used_at=float(score),
        )
        for raw, score in rows
    ]
