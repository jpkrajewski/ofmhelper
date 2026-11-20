from pathlib import Path
from unittest import mock

import pytest
from redis.exceptions import RedisError
from signin import make_user

from ofmhelpers.db.users import store as users
from ofmhelpers.db.users.schema import Role
from ofmhelpers.features.assets import ref_usage

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

    assert [ref.path for ref in ref_usage.recent(ANN, 5)] == [
        str(Path("uploads/assets/b.png")),
        str(Path("uploads/assets/a.png")),
    ]


def test_picking_a_file_again_moves_it_back_to_the_front():
    ref_usage.record_use(ANN, Path("a.png"))
    ref_usage.record_use(ANN, Path("b.png"))
    ref_usage.record_use(ANN, Path("a.png"))

    assert [ref.path for ref in ref_usage.recent(ANN, 5)] == ["a.png", "b.png"]


def test_each_users_list_holds_only_their_own_picks(monkeypatch):
    monkeypatch.setenv("OFM_REF_USAGE_MAX_TRACKED", str(3))
    ref_usage.record_use(ANN, Path("ann.png"))
    for i in range(5):
        ref_usage.record_use(BOB, Path(f"bob{i}.png"))

    assert [ref.path for ref in ref_usage.recent(ANN, 10)] == ["ann.png"]
    assert [ref.path for ref in ref_usage.recent(BOB, 10)] == [
        "bob4.png",
        "bob3.png",
        "bob2.png",
    ]


def test_the_key_is_bounded(monkeypatch):
    monkeypatch.setenv("OFM_REF_USAGE_MAX_TRACKED", str(3))
    for i in range(6):
        ref_usage.record_use(ANN, Path(f"{i}.png"))

    assert [ref.path for ref in ref_usage.recent(ANN, 10)] == [
        "5.png",
        "4.png",
        "3.png",
    ]


def test_a_dead_broker_costs_ordering_not_the_generation():
    with mock.patch.object(ref_usage, "get_redis", side_effect=RedisError("down")):
        ref_usage.record_use(ANN, Path("a.png"))
        assert ref_usage.recent(ANN, 5) == []


def test_a_va_and_their_owner_share_one_list():
    owner, other = make_user("boss@example.com"), make_user("second@example.com")
    va = make_user("va@example.com", Role.VA)
    users.set_owner(va.id, owner.id)
    ref_usage.get_redis().delete(ref_usage._key(owner.id), ref_usage._key(other.id))

    ref_usage.record_use(owner.id, Path("owner.png"))
    ref_usage.record_use(va.id, Path("va.png"))

    assert ref_usage._key(va.id) == ref_usage._key(owner.id)
    for person in (owner, va):
        assert [ref.path for ref in ref_usage.recent(person.id, 5)] == [
            "va.png",
            "owner.png",
        ]
    assert ref_usage.recent(other.id, 5) == []
    ref_usage.get_redis().delete(ref_usage._key(owner.id))
