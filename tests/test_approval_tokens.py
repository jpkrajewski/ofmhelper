"""
Covers web/approval_tokens.py: the single-use "magic link" token store behind
the Discord approval flow (see web/routers/approve.py). Backed by Postgres now
(conftest truncates the table between tests), so no on-disk isolation is
needed. Expiry is exercised by swapping the repository's clock forward.
"""

import time

from ofmhelpers.web import approval_tokens
from ofmhelpers.web.db import repository


class _FrozenClock:
    def __init__(self, value: float):
        self._value = value

    def time(self) -> float:
        return self._value


def test_create_then_get():
    token = approval_tokens.create_token("todo1", "/path/to/asset.png")

    record = approval_tokens.get_token(token)
    assert record is not None
    assert record["todo_id"] == "todo1"
    assert record["asset_path"] == "/path/to/asset.png"
    assert record["used_at"] is None


def test_get_unknown_token_returns_none():
    assert approval_tokens.get_token("does-not-exist") is None


def test_consume_happy_path_marks_used():
    token = approval_tokens.create_token("todo1", "/path/to/asset.png")

    assert approval_tokens.consume(token, "/path/to/asset.png") == "ok"

    record = approval_tokens.get_token(token)
    assert record["used_at"] is not None


def test_consume_twice_fails_second_time():
    token = approval_tokens.create_token("todo1", "/path/to/asset.png")

    assert approval_tokens.consume(token, "/path/to/asset.png") == "ok"
    assert approval_tokens.consume(token, "/path/to/asset.png") == "used"


def test_consume_unknown_token():
    assert approval_tokens.consume("garbage", "/path/to/asset.png") == "not_found"


def test_consume_expired_token(monkeypatch):
    token = approval_tokens.create_token("todo1", "/path/to/asset.png")

    # Simulate time passing well beyond the TTL.
    monkeypatch.setattr(repository, "time", _FrozenClock(time.time() + 10**9))

    assert approval_tokens.consume(token, "/path/to/asset.png") == "expired"


def test_consume_stale_when_asset_path_changed():
    """A VA replacing the asset after the Discord message went out must not
    let the old link approve the new (unreviewed) file."""
    token = approval_tokens.create_token("todo1", "/path/to/original.png")

    assert approval_tokens.consume(token, "/path/to/replaced.png") == "stale"
    # Still unused -- a stale check must not burn the token.
    assert approval_tokens.get_token(token)["used_at"] is None


def test_expired_tokens_are_pruned_on_save(monkeypatch):
    old_token = approval_tokens.create_token("todo1", "/path/a.png")

    monkeypatch.setattr(repository, "time", _FrozenClock(time.time() + 10**9))

    # Any create triggers a purge of expired entries.
    approval_tokens.create_token("todo2", "/path/b.png")

    assert approval_tokens.get_token(old_token) is None
