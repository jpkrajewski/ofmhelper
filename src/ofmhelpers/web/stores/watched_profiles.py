"""
ofmhelpers/web/stores/watched_profiles.py

The Instagram profiles /reels keeps rescanning (routers/downloads/reels.py
adds and removes them, web/reel_watch.py rescans them) and each one's latest
scan, so the page opens with results instead of an empty list.
"""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING

from ofmhelpers.web.db.repositories import WatchedProfileRepository
from ofmhelpers.web.schemas import WatchedProfile

if TYPE_CHECKING:
    from ofmhelpers.instagram import ProfileReels


@lru_cache(maxsize=1)
def _repository() -> WatchedProfileRepository:
    """Built on first use rather than at import (constructing it binds a Redis
    connection for its cache)."""
    return WatchedProfileRepository()


def list_watched(group: str | None = None) -> list[WatchedProfile]:
    """Every watched profile, or only `group`'s."""
    profiles = [WatchedProfile.model_validate(row) for row in _repository().list_all()]
    return [p for p in profiles if group is None or p.group == group]


def list_groups() -> list[str]:
    return _repository().list_groups()


def create_group(name: str) -> None:
    _repository().create_group(name)


def get_watched(username: str) -> WatchedProfile | None:
    return next((p for p in list_watched() if p.username == username), None)


def watch(username: str, group: str | None = None) -> bool:
    """True when the profile is new; one already watched is only moved to
    `group` (when given)."""
    return _repository().add(username, group)


def set_group(username: str, group: str | None) -> bool:
    """Moves a watched profile to `group` (None: no group). False if the
    profile is not watched."""
    return _repository().set_group(username, group)


def unwatch(username: str) -> bool:
    """Returns False if the profile was not watched."""
    return _repository().delete(username)


def claim_due(interval_s: float, claim_ttl_s: float) -> list[str]:
    return _repository().claim_due(interval_s, claim_ttl_s)


def record_scan(
    username: str, result: ProfileReels | None, error: str | None = None
) -> None:
    _repository().record_scan(
        username, result.model_dump(mode="json") if result else None, error
    )


def replace_result(username: str, result: ProfileReels) -> None:
    _repository().replace_result(username, result.model_dump(mode="json"))
