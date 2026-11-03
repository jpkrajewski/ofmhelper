"""
When each shared reference file was last *used*, on the Redis connection the
RQ queue already owns (ofmhelpers.cache) -- shared state, so the ordering holds
across every uvicorn worker.

This exists so the picker can show two genuinely different lists (routers/
refs.py): the files you last picked, and the files you last uploaded. It used
to be one list, because reuse `touch()`ed the file and mtime meant both at
once -- which also meant a resolver quietly wrote to the asset store.

Not a Postgres table: this is picker ordering, not a noun the app owns.
Losing it (a flushed Redis, a fresh deploy) degrades the picker to
"most recently uploaded", which is exactly what it did before.

Kept per user (one key each): with per-user asset roots, one user's picks
would otherwise push everyone else's out of the window the picker reads.
An admin's list is their own picks, like anyone's.

Redis failures are swallowed, like ratelimit.py's: a broker that can't record
"you picked this file" is no reason to fail a generation that has every file
it needs.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from redis.exceptions import RedisError

from ofmhelpers.cache import get_redis
from ofmhelpers.config import settings
from ofmhelpers.log import get_logger

if TYPE_CHECKING:
    from pathlib import Path

logger = get_logger(__name__)

_KEY_PREFIX = "refs:used:"
# Bounded so a long-lived deployment doesn't grow one key forever. The picker
# asks for a handful; anything past this is older than "recently used" means.
_MAX_TRACKED = settings.web.ref_usage_max_tracked


def _key(user_id: int) -> str:
    return f"{_KEY_PREFIX}{user_id}"


def record_use(user_id: int, path: Path) -> None:
    """Remember that `user_id` just picked this file."""
    key = _key(user_id)
    try:
        redis = get_redis()
        redis.zadd(key, {str(path): time.time()})
        # Trim to the newest _MAX_TRACKED by rank (0 = oldest).
        redis.zremrangebyrank(key, 0, -(_MAX_TRACKED + 1))
    except RedisError:
        logger.warning("could not record use of %s", path, exc_info=True)


def recent(user_id: int, limit: int) -> list[tuple[str, float]]:
    """`user_id`'s `limit` most recently used paths, newest first, each with
    the timestamp it was used at. Empty when Redis is unreachable."""
    try:
        # withscores=True narrows the stubs' union to (member, score) pairs.
        rows: Any = get_redis().zrevrange(_key(user_id), 0, limit - 1, withscores=True)
    except RedisError:
        logger.warning("could not read recently-used refs", exc_info=True)
        return []
    return [
        (raw.decode() if isinstance(raw, bytes) else str(raw), float(score))
        for raw, score in rows
    ]
