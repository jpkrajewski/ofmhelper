# Copyright (C) 2026 jpkrajewski

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from ofmhelpers.cache import lock


class _Lock:
    def __init__(self, *, refuse: bool) -> None:
        self.refuse = refuse
        self.released = False

    def acquire(self) -> bool:
        if self.refuse:
            msg = "Connection refused"
            raise RedisConnectionError(msg)
        return True

    def release(self) -> None:
        self.released = True


class _Redis:
    def __init__(self, held: _Lock) -> None:
        self.held = held

    def lock(self, _name: str, **_options: int) -> _Lock:
        return self.held


def _redis(monkeypatch: pytest.MonkeyPatch, *, refuse: bool) -> _Lock:
    held = _Lock(refuse=refuse)
    monkeypatch.setattr(lock, "get_redis", lambda: _Redis(held))
    return held


def test_lock_held_says_no_when_redis_is_down_and_releases_nothing(monkeypatch):
    held = _redis(monkeypatch, refuse=True)

    with lock.lock_held("storage:bin:1", 10) as got:
        assert got is False

    assert held.released is False


def test_lock_held_says_yes_and_releases_after(monkeypatch):
    held = _redis(monkeypatch, refuse=False)

    with lock.lock_held("storage:bin:1", 10) as got:
        assert got is True
        assert held.released is False

    assert held.released is True


def test_locked_still_goes_ahead_when_redis_is_down(monkeypatch):
    _redis(monkeypatch, refuse=True)
    ran = []

    with lock.locked("threads:start:1", 10):
        ran.append(True)

    assert ran == [True]
