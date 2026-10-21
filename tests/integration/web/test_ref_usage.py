"""
web/ref_usage.py: which reference files were last *picked*, kept in Redis so
mtime can go back to meaning "uploaded" (see routers/refs.py's two lists, and
task_helpers.resolve_existing_ref, which no longer writes to the store).

The important property is the one at the bottom: a broker that can't record
"you picked this file" must not fail a generation that has every file it
needs.
"""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ["APP_PASSWORD_VA"] = "test-va"
os.environ.setdefault("SESSION_SECRET", "test-secret")

from pathlib import Path
from unittest import mock

import pytest
from redis.exceptions import RedisError

from ofmhelpers.web import ref_usage


@pytest.fixture(autouse=True)
def clean_key():
    ref_usage.get_redis().delete(ref_usage._KEY)
    yield
    ref_usage.get_redis().delete(ref_usage._KEY)


def test_records_uses_newest_first():
    ref_usage.record_use(Path("uploads/assets/a.png"))
    ref_usage.record_use(Path("uploads/assets/b.png"))

    assert [path for path, _score in ref_usage.recent(5)] == [
        str(Path("uploads/assets/b.png")),
        str(Path("uploads/assets/a.png")),
    ]


def test_picking_a_file_again_moves_it_back_to_the_front():
    """The whole point: a file you use daily can't sink below one you touched
    once."""
    ref_usage.record_use(Path("a.png"))
    ref_usage.record_use(Path("b.png"))
    ref_usage.record_use(Path("a.png"))

    assert [path for path, _score in ref_usage.recent(5)] == ["a.png", "b.png"]


def test_the_key_is_bounded(monkeypatch):
    monkeypatch.setattr(ref_usage, "_MAX_TRACKED", 3)
    for i in range(6):
        ref_usage.record_use(Path(f"{i}.png"))

    assert [path for path, _score in ref_usage.recent(10)] == [
        "5.png",
        "4.png",
        "3.png",
    ]


def test_a_dead_broker_costs_ordering_not_the_generation():
    with mock.patch.object(ref_usage, "get_redis", side_effect=RedisError("down")):
        ref_usage.record_use(Path("a.png"))  # must not raise
        assert ref_usage.recent(5) == []
