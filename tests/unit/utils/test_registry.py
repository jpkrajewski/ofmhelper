"""`utils/registry.py`: the read-only name -> entry table functions return."""

import pytest
from pydantic import ValidationError

from ofmhelpers.utils.registry import Registry


class _Thing:
    name = "thing"


def test_a_registry_answers_by_name_and_keeps_its_order():
    registry = Registry[int](entries={"b": 2, "a": 1})

    assert registry["b"] == 2
    assert registry.get("a") == 1
    assert registry.get("missing") is None
    assert "a" in registry
    assert "missing" not in registry
    assert registry.names() == ["b", "a"]
    assert registry.all() == [2, 1]
    assert len(registry) == 2


def test_an_unknown_name_is_a_key_error():
    with pytest.raises(KeyError):
        Registry[int](entries={})["missing"]


def test_a_registry_holds_classes_and_plain_objects():
    thing = _Thing()

    assert Registry[type[_Thing]](entries={"thing": _Thing})["thing"] is _Thing
    assert Registry[_Thing](entries={"thing": thing})["thing"] is thing


def test_a_registry_cannot_be_reassigned():
    registry = Registry[int](entries={"a": 1})

    with pytest.raises(ValidationError):
        registry.entries = {}
