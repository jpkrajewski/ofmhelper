"""
ofmhelpers/web/instagram_stats.py

Latest-scrape stats (followers + last-3-posts views/likes) for each
Instagram account in the models roster. Mirrors models.py: thin wrapper
over web/db/repository.py, the only DB-touching layer.
"""

from __future__ import annotations

from ofmhelpers.web.db.repository import InstagramStatsRepository

_repo = InstagramStatsRepository()


def save_stats(
    account_id: str, followers: int | None, posts: list[dict], error: str | None
) -> dict:
    return _repo.upsert(account_id, followers, posts, error)


def get_stats(account_id: str) -> dict | None:
    return _repo.get(account_id)


def get_stats_many(account_ids: list[str]) -> dict[str, dict]:
    """Keyed by account_id; accounts never scraped yet are simply absent."""
    return _repo.get_many(tuple(account_ids))
