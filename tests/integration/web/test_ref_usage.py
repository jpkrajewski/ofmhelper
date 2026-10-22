"""
web/ref_usage.py: which reference files were last *picked*, kept in Redis so
mtime can go back to meaning "uploaded" (see routers/refs.py's two lists, and
task_helpers.resolve_existing_ref, which no longer writes to the store).

Each user has their own list: one user's picks never push another's out.
The important property is the one at the bottom: a broker that can't record
"you picked this file" must not fail a generation that has every file it
needs.
"""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ.setdefault("SESSION_SECRET", "test-secret")

from pathlib import Path
from unittest import mock

import pytest
from redis.exceptions import RedisError

from ofmhelpers.web import ref_usage

# Ids no real user has: the keys are the tests' own.
ANN, BOB = 990_001, 990_002


@pytest.fixture(autouse=True)
def clean_keys():
    keys = [ref_usage._key(ANN), ref_usage._key(BOB)]
    ref_usage.get_redis().delete(*keys)
    yield
    ref_usage.get_redis().delete(*keys)


def test_records_uses_newest_first():
    ref_usage.record_use(ANN, Path("uploads/assets/a.png"))
    ref_usage.record_use(ANN, Path("uploads/assets/b.png"))

    assert [path for path, _score in ref_usage.recent(ANN, 5)] == [
        str(Path("uploads/assets/b.png")),
        str(Path("uploads/assets/a.png")),
    ]


def test_picking_a_file_again_moves_it_back_to_the_front():
    """The whole point: a file you use daily can't sink below one you touched
    once."""
    ref_usage.record_use(ANN, Path("a.png"))
    ref_usage.record_use(ANN, Path("b.png"))
    ref_usage.record_use(ANN, Path("a.png"))

    assert [path for path, _score in ref_usage.recent(ANN, 5)] == ["a.png", "b.png"]


def test_each_users_list_holds_only_their_own_picks(monkeypatch):
    """With one shared list, a busy user's picks filled the bounded window
    and everyone else's recent files fell out of it."""
    monkeypatch.setattr(ref_usage, "_MAX_TRACKED", 3)
    ref_usage.record_use(ANN, Path("ann.png"))
    for i in range(5):
        ref_usage.record_use(BOB, Path(f"bob{i}.png"))

    assert [path for path, _score in ref_usage.recent(ANN, 10)] == ["ann.png"]
    assert [path for path, _score in ref_usage.recent(BOB, 10)] == [
        "bob4.png",
        "bob3.png",
        "bob2.png",
    ]


def test_the_key_is_bounded(monkeypatch):
    monkeypatch.setattr(ref_usage, "_MAX_TRACKED", 3)
    for i in range(6):
        ref_usage.record_use(ANN, Path(f"{i}.png"))

    assert [path for path, _score in ref_usage.recent(ANN, 10)] == [
        "5.png",
        "4.png",
        "3.png",
    ]


def test_a_dead_broker_costs_ordering_not_the_generation():
    with mock.patch.object(ref_usage, "get_redis", side_effect=RedisError("down")):
        ref_usage.record_use(ANN, Path("a.png"))  # must not raise
        assert ref_usage.recent(ANN, 5) == []
