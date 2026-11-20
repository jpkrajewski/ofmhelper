from functools import lru_cache

from ofmhelpers.db.users.store import agency_id
from ofmhelpers.db.watched_profiles.repository import WatchedProfileRepository
lazy from ofmhelpers.db.watched_profiles.schema import ProfileReels, WatchedProfile


@lru_cache(maxsize=1)
def _repository() -> WatchedProfileRepository:
    return WatchedProfileRepository()


def list_watched(
    group: str | None = None, owner: int | None = None
) -> list[WatchedProfile]:
    profiles = [
        p for p in _repository().list_all(owner) if group is None or p.group == group
    ]
    if owner is None:
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
    return next((p for p in list_watched() if p.username == username), None)


def watchers(username: str) -> list[int]:
    return [
        row.user_id for row in _repository().list_all(None) if row.username == username
    ]


def watch(
    username: str, group: str | None = None, *, user_id: int | None = None
) -> bool:
    return _repository().add(
        username, group, agency_id() if user_id is None else user_id
    )


def set_group(username: str, group: str | None, user_id: int | None = None) -> bool:
    return _repository().set_group(
        username, group, agency_id() if user_id is None else user_id
    )


def unwatch(username: str, user_id: int | None = None) -> bool:
    return _repository().delete(username, agency_id() if user_id is None else user_id)


def unwatch_unscanned(username: str) -> bool:
    return _repository().delete_unscanned(username)


def claim_due(interval_s: float | None, claim_ttl_s: float) -> list[str]:
    return _repository().claim_due(interval_s, claim_ttl_s)


def record_scan(
    username: str, result: ProfileReels | None, error: str | None = None
) -> None:
    _repository().record_scan(
        username, result.model_dump(mode="json") if result else None, error
    )


def replace_result(username: str, result: ProfileReels) -> None:
    _repository().replace_result(username, result.model_dump(mode="json"))
