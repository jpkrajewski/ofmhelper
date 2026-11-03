"""A /clone-links Generate stopped by its VA (Stop): the runs not yet paid
refuse to start (`run.run_clone_link` checks `stopped` before it downloads
and again before it pays). A run already paid is never stopped: recovery
finishes it.

One Redis flag per Generate (`clone-links:stop:<batch>`), kept for as long
as recovery may still re-run one of its runs (`settings.aigen.
resume_max_age_s`). Nothing else changes: no job is failed or forgotten
here -- a worker may hold it past its payment."""

from redis.exceptions import RedisError

from ofmhelpers.cache import get_redis
from ofmhelpers.config import settings
from ofmhelpers.log import get_logger

logger = get_logger(__name__)


def _key(batch: str) -> str:
    return f"clone-links:stop:{batch}"


def stop_batch(batch: str) -> None:
    """Every run of `batch` not yet paid refuses to start from now on.
    Raises `RedisError` when Redis is down: a Stop that did not happen must
    not look done."""
    get_redis().set(_key(batch), "1", ex=settings.aigen.resume_max_age_s)
    logger.info("clone-links: batch %s stopped", batch)


def stopped(batch: str | None) -> bool:
    """Whether `batch` was stopped (None: a run of no Generate). Redis down:
    not stopped (logged) -- its runs were confirmed, and the queue they come
    from is down with it."""
    if not batch:
        return False
    try:
        return bool(get_redis().exists(_key(batch)))
    except RedisError:
        logger.warning("clone-links: stop of batch %s unreadable", batch, exc_info=True)
        return False
