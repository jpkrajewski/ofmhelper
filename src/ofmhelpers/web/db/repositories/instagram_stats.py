"""Follower/last-N-reels numbers behind the /models page."""

from __future__ import annotations

import time

from sqlalchemy import select

from ofmhelpers.web.db.models import (
    InstagramStatsRow,
)
from ofmhelpers.web.db.repositories.cached_repository import (
    CachedRepository,
    cached,
    invalidates_cache,
)
from ofmhelpers.web.db.session import session_scope


def _instagram_stats_to_dict(row: InstagramStatsRow) -> dict:
    return {
        "account_id": row.account_id,
        "followers": row.followers,
        "posts": row.posts or [],
        "checked_at": row.checked_at,
        "error": row.error,
    }


class InstagramStatsRepository(CachedRepository):
    """One row per instagram account, always the latest scrape -- see
    instagram_public.py for what's scraped and why `posts[i].shares` is
    always None."""

    cache_namespace = "instagram_stats"

    @invalidates_cache
    def upsert(
        self,
        account_id: str,
        followers: int | None,
        posts: list[dict],
        error: str | None,
    ) -> dict:
        with session_scope() as s:
            row = s.get(InstagramStatsRow, account_id)
            if row is None:
                row = InstagramStatsRow(account_id=account_id)
                s.add(row)
            row.followers = followers
            row.posts = posts
            row.checked_at = time.time()
            row.error = error
            s.flush()
            return _instagram_stats_to_dict(row)

    @cached
    def get(self, account_id: str) -> dict | None:
        with session_scope() as s:
            row = s.get(InstagramStatsRow, account_id)
            return _instagram_stats_to_dict(row) if row is not None else None

    @cached
    def get_many(self, account_ids: tuple[str, ...]) -> dict[str, dict]:
        if not account_ids:
            return {}
        with session_scope() as s:
            rows = (
                s.execute(
                    select(InstagramStatsRow).where(
                        InstagramStatsRow.account_id.in_(account_ids)
                    )
                )
                .scalars()
                .all()
            )
            return {r.account_id: _instagram_stats_to_dict(r) for r in rows}
