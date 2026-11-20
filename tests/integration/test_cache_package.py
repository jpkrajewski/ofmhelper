import importlib

import pytest
from redis import Redis
from redis.exceptions import RedisError
from repo_paths import SRC

import ofmhelpers.cache.queue as queue_module
from ofmhelpers.cache import delete_text, get_queue, get_redis, get_text, set_text


def test_only_one_module_constructs_a_redis_connection():
    offenders = [
        p.relative_to(SRC).as_posix()
        for p in SRC.rglob("*.py")
        if "Redis.from_url" in p.read_text(encoding="utf-8")
    ]
    assert offenders == ["cache/redis.py"]


def test_the_old_module_locations_are_gone():
    for name in (
        "ofmhelpers.web.queue",
        "ofmhelpers.web.ratelimit",
        "ofmhelpers.db.cache",
    ):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(name)


def test_get_redis_is_one_connection_per_url():
    assert get_redis() is get_redis()
    assert isinstance(get_redis(), Redis)


def test_the_queue_binds_the_shared_connection():
    assert get_queue().connection is get_redis()


def test_text_helpers_roundtrip():
    set_text("test:cache:k", "v", 60)
    assert get_text("test:cache:k") == "v"
    delete_text("test:cache:k")
    assert get_text("test:cache:k") is None


def test_text_helpers_report_a_miss_when_the_broker_is_down(monkeypatch):

    def boom():
        msg = "down"
        raise RedisError(msg)

    monkeypatch.setattr("ofmhelpers.cache.redis.get_redis", boom)

    assert get_text("test:cache:k") is None
    set_text("test:cache:k", "v", 60)
    delete_text("test:cache:k")


def test_a_job_can_carry_its_own_time_limit(monkeypatch):
    monkeypatch.setenv("OFM_RQ_ASYNC", "true")
    monkeypatch.setenv("OFM_RQ_JOB_TIMEOUT_S", "2700")

    long_job = queue_module.enqueue(print, "a", job_timeout=6900)
    other_job = queue_module.enqueue(print, "b")

    assert [long_job.timeout, other_job.timeout] == [6900, 2700]
    queue_module.get_redis().flushdb()


def test_inline_mode_never_hands_the_time_limit_to_the_function(monkeypatch):
    monkeypatch.setenv("OFM_RQ_ASYNC", "false")
    seen = []
    queue_module.enqueue(lambda *a, **k: seen.append((a, k)), 1, job_timeout=9, x=2)
    assert seen == [((1,), {"x": 2})]
