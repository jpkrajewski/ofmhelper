"""
ofmhelpers/web/stores/watched_profiles.py

The Instagram profiles /reels keeps rescanning (routers/downloads/reels.py
adds and removes them, web/reel_watch.py rescans them) and each one's latest
scan, so the page opens with results instead of an empty list. Each user
has their own list and groups (`owner`; None: everyone's, for the sweeper,
a profile two users watch listed once; a session always passes its own id); a username is scanned once.
"""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING

from ofmhelpers.web.db.repositories import WatchedProfileRepository
from ofmhelpers.web.schemas import WatchedProfile
from ofmhelpers.web.stores.users import agency_id

if TYPE_CHECKING:
    from ofmhelpers.instagram import ProfileReels


@lru_cache(maxsize=1)
def _repository() -> WatchedProfileRepository:
    """Built on first use rather than at import (constructing it binds a Redis
    connection for its cache)."""
    return WatchedProfileRepository()


def list_watched(
    group: str | None = None, owner: int | None = None
) -> list[WatchedProfile]:
    """`owner`'s watched profiles (everyone's, each username once, for
    None), or only `group`'s."""
    profiles = [
        p
        for p in map(WatchedProfile.model_validate, _repository().list_all(owner))
        if group is None or p.group == group
    ]
    if owner is None:
        # After the group filter: a profile one user has in `group` stays in
        # its feed even when an older row of it (another user's) is not.
        first: dict[str, WatchedProfile] = {}
        for p in profiles:
            first.setdefault(p.username, p)
        profiles = list(first.values())
    return profiles


def list_groups(owner: int | None = None) -> list[str]:
    return _repository().list_groups(owner)


def create_group(name: str, user_id: int | None = None) -> None:
    _repository().create_group(name, agency_id() if user_id is None else user_id)


def get_watched(username: str) -> WatchedProfile | None:
    """The username's row (any owner's: they share one scan)."""
    return next((p for p in list_watched() if p.username == username), None)


def watchers(username: str) -> list[int]:
    """Who watches `username`, earliest-added first (whose login its
    shared scan uses: `stores.cookies.for_watched`)."""
    return [
        row["user_id"]
        for row in _repository().list_all(None)
        if row["username"] == username
    ]


def watch(
    username: str, group: str | None = None, *, user_id: int | None = None
) -> bool:
    """True when the profile is new to `user_id`'s list (None: the
    agency's, `users.agency_id`); one already on it is only moved to
    `group` (when given)."""
    return _repository().add(
        username, group, agency_id() if user_id is None else user_id
    )


def set_group(username: str, group: str | None, user_id: int | None = None) -> bool:
    """Moves `user_id`'s watched profile (None: the agency's) to `group`
    (None: no group); nobody else's row of it. False if it is not on their
    list."""
    return _repository().set_group(
        username, group, agency_id() if user_id is None else user_id
    )


def unwatch(username: str, user_id: int | None = None) -> bool:
    """Off `user_id`'s list (None: the agency's) only. False if it was not
    on it."""
    return _repository().delete(username, agency_id() if user_id is None else user_id)


def unwatch_unscanned(username: str) -> bool:
    """A just-added profile that can never be scanned leaves every list
    that has no scan of it."""
    return _repository().delete_unscanned(username)


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
