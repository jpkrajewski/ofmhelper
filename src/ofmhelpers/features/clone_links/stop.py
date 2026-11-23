# Copyright (C) 2026 jpkrajewski

from redis.exceptions import RedisError

from ofmhelpers.cache import get_redis
from ofmhelpers.config import settings
from ofmhelpers.features.clone_links.constants import clone_links_constants
from ofmhelpers.log import get_logger

logger = get_logger(__name__)


def _key(batch: str) -> str:
    return clone_links_constants().stop_key.format(batch=batch)


def stop_batch(batch: str) -> None:
    get_redis().set(_key(batch), "1", ex=settings.aigen.resume_max_age_s)
    logger.info("clone-links: batch %s stopped", batch)


def stopped(batch: str | None) -> bool:
    if not batch:
        return False
    try:
        return bool(get_redis().exists(_key(batch)))
    except RedisError:
        logger.warning("clone-links: stop of batch %s unreadable", batch, exc_info=True)
        return False
