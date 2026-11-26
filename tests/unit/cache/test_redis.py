# Copyright (C) 2026 jpkrajewski

import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from redis.exceptions import RedisError

import ofmhelpers.cache.redis as redis_module
from ofmhelpers.cache.redis import get_redis


class _FakeRedis:
    def __init__(self) -> None:
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1


def test_redis_is_reused_and_closed_when_its_url_changes(monkeypatch):
    created = []

    def make_redis(url):
        created.append(url)
        return _FakeRedis()

    monkeypatch.setattr(redis_module.Redis, "from_url", make_redis)
    monkeypatch.setenv("OFM_REDIS_URL", "redis://first")

    first = get_redis()

    assert get_redis() is first
    assert first.close_calls == 0

    monkeypatch.setenv("OFM_REDIS_URL", "redis://second")
    second = get_redis()

    assert second is not first
    assert first.close_calls == 1
    assert second.close_calls == 0
    assert created == ["redis://first", "redis://second"]


def test_redis_is_constructed_once_for_concurrent_calls(monkeypatch):
    created = []

    class SlowRedisManager(redis_module._RedisManager):
        def __init__(self):
            super().__init__()
            time.sleep(0.02)

    def make_redis(url):
        created.append(url)
        time.sleep(0.02)
        return _FakeRedis()

    monkeypatch.setattr(redis_module.Redis, "from_url", make_redis)
    monkeypatch.setattr(redis_module, "_RedisManager", SlowRedisManager)
    monkeypatch.setenv("OFM_REDIS_URL", "redis://concurrent")
    redis_module._redis_manager.cache_clear()

    with ThreadPoolExecutor(max_workers=8) as pool:
        clients = list(pool.map(lambda _: get_redis(), range(8)))

    assert created == ["redis://concurrent"]
    assert all(client is clients[0] for client in clients)


def test_redis_replacement_is_constructed_once_for_concurrent_calls(monkeypatch):
    created = []

    def make_redis(url):
        created.append(url)
        if url == "redis://replacement":
            time.sleep(0.02)
        return _FakeRedis()

    monkeypatch.setattr(redis_module.Redis, "from_url", make_redis)
    monkeypatch.setenv("OFM_REDIS_URL", "redis://replacement-original")
    first = get_redis()

    monkeypatch.setenv("OFM_REDIS_URL", "redis://replacement")
    with ThreadPoolExecutor(max_workers=8) as pool:
        clients = list(pool.map(lambda _: get_redis(), range(8)))

    assert created == ["redis://replacement-original", "redis://replacement"]
    assert all(client is clients[0] for client in clients)
    assert clients[0] is not first
    assert first.close_calls == 1


def test_redis_replacement_failure_keeps_the_current_client(monkeypatch):
    def make_redis(_url):
        return _FakeRedis()

    def fail_to_make_redis(_url):
        msg = "replacement failed"
        raise RedisError(msg)

    monkeypatch.setattr(redis_module.Redis, "from_url", make_redis)
    monkeypatch.setenv("OFM_REDIS_URL", "redis://original")
    first = get_redis()

    monkeypatch.setattr(redis_module.Redis, "from_url", fail_to_make_redis)
    monkeypatch.setenv("OFM_REDIS_URL", "redis://unavailable")
    with pytest.raises(RedisError, match="replacement failed"):
        get_redis()

    assert first.close_calls == 0
    monkeypatch.setenv("OFM_REDIS_URL", "redis://original")
    assert get_redis() is first
