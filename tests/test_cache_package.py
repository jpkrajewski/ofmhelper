"""The `ofmhelpers.cache` package is where Redis lives, and it is the *only*
place it lives.

The invariant is worth a test rather than a comment: a second `Redis.from_url`
somewhere else would silently bind to a different pool -- and, in the test
suite, to whatever URL was set at that module's import time rather than the
fixture's. That is exactly the class of bug the single lazy accessor exists to
prevent, and it fails quietly.
"""

import importlib
import pathlib

import pytest
from redis import Redis
from redis.exceptions import RedisError

from ofmhelpers.cache import delete_text, get_queue, get_redis, get_text, set_text

SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "ofmhelpers"


def test_only_one_module_constructs_a_redis_connection():
    offenders = [
        p.relative_to(SRC).as_posix()
        for p in SRC.rglob("*.py")
        if "Redis.from_url" in p.read_text(encoding="utf-8")
    ]
    assert offenders == ["cache/redis.py"]


def test_the_old_module_locations_are_gone():
    """web/queue.py, web/ratelimit.py and web/db/cache.py all moved. Leaving a
    shim behind would let a new caller re-fork the connection."""
    for name in (
        "ofmhelpers.web.queue",
        "ofmhelpers.web.ratelimit",
        "ofmhelpers.web.db.cache",
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
    """Optimisation caches must never turn a broker outage into a failure."""

    def boom():
        msg = "down"
        raise RedisError(msg)

    monkeypatch.setattr("ofmhelpers.cache.redis.get_redis", boom)

    assert get_text("test:cache:k") is None
    set_text("test:cache:k", "v", 60)  # must not raise
    delete_text("test:cache:k")  # must not raise
