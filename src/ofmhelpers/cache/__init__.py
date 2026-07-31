"""Everything this app keeps in Redis: the connection itself, the RQ queue
built on it, and the small key/value helpers used for pure optimisation caches.

`redis.py` holds **the only Redis connection in the repo** -- every other module
(the RQ queue, the repository cache-aside layer, the rate-limit counters, the
reference-file usage set, the kie.ai upload cache) goes through `get_redis()`,
so there is one URL, one pool, one place a test points at a test broker.
"""

from ofmhelpers.cache.queue import QUEUE_NAME, enqueue, get_queue
from ofmhelpers.cache.redis import (
    delete_text,
    get_redis,
    get_text,
    set_text,
)

__all__ = [
    "QUEUE_NAME",
    "delete_text",
    "enqueue",
    "get_queue",
    "get_redis",
    "get_text",
    "set_text",
]
