"""
ofmhelpers/web/stores/instagram_stats.py

Latest-scrape stats (followers + last-3-posts views/likes) for each
Instagram account in the models roster. Mirrors models.py: thin wrapper
over web/db/repository.py, the only DB-touching layer.
"""

from __future__ import annotations

from functools import lru_cache

from ofmhelpers.web.db.repositories import InstagramStatsRepository


@lru_cache(maxsize=1)
def _repository() -> InstagramStatsRepository:
    """The process-wide Instagram-stats repository, built on first use rather than at
    import. Lazy because constructing it binds a Redis connection for its
    cache, and at import time OFM_REDIS_URL may not be its final value yet."""
    return InstagramStatsRepository()


def save_stats(
    account_id: str, followers: int | None, posts: list[dict], error: str | None
) -> dict:
    return _repository().upsert(account_id, followers, posts, error)


def get_stats(account_id: str) -> dict | None:
    return _repository().get(account_id)


def get_stats_many(account_ids: list[str]) -> dict[str, dict]:
    """Keyed by account_id; accounts never scraped yet are simply absent."""
    return _repository().get_many(tuple(account_ids))
