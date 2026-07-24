"""
Covers web/approval_tokens.py: the single-use "magic link" token store behind
the Discord approval flow (see web/routers/approve.py).
"""

from ofmhelpers.web import approval_tokens


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(approval_tokens, "STORE_FILE", tmp_path / "tokens.json")


def test_create_then_get(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    token = approval_tokens.create_token("todo1", "/path/to/asset.png")

    record = approval_tokens.get_token(token)
    assert record is not None
    assert record["todo_id"] == "todo1"
    assert record["asset_path"] == "/path/to/asset.png"
    assert record["used_at"] is None


def test_get_unknown_token_returns_none(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    assert approval_tokens.get_token("does-not-exist") is None


def test_consume_happy_path_marks_used(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    token = approval_tokens.create_token("todo1", "/path/to/asset.png")

    assert approval_tokens.consume(token, "/path/to/asset.png") == "ok"

    record = approval_tokens.get_token(token)
    assert record["used_at"] is not None


def test_consume_twice_fails_second_time(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    token = approval_tokens.create_token("todo1", "/path/to/asset.png")

    assert approval_tokens.consume(token, "/path/to/asset.png") == "ok"
    assert approval_tokens.consume(token, "/path/to/asset.png") == "used"


def test_consume_unknown_token(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    assert approval_tokens.consume("garbage", "/path/to/asset.png") == "not_found"


def test_consume_expired_token(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    token = approval_tokens.create_token("todo1", "/path/to/asset.png")

    # Simulate time passing well beyond the TTL.
    future = approval_tokens.time.time() + approval_tokens.TOKEN_TTL_SECONDS + 10
    monkeypatch.setattr(approval_tokens.time, "time", lambda: future)

    assert approval_tokens.consume(token, "/path/to/asset.png") == "expired"


def test_consume_stale_when_asset_path_changed(monkeypatch, tmp_path):
    """A VA replacing the asset after the Discord message went out must not
    let the old link approve the new (unreviewed) file."""
    _isolate(monkeypatch, tmp_path)
    token = approval_tokens.create_token("todo1", "/path/to/original.png")

    assert approval_tokens.consume(token, "/path/to/replaced.png") == "stale"
    # Still unused -- a stale check must not burn the token.
    assert approval_tokens.get_token(token)["used_at"] is None


def test_expired_tokens_are_pruned_on_save(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    old_token = approval_tokens.create_token("todo1", "/path/a.png")

    future = approval_tokens.time.time() + approval_tokens.TOKEN_TTL_SECONDS + 10
    monkeypatch.setattr(approval_tokens.time, "time", lambda: future)

    # Any create/consume triggers a save, which prunes expired entries.
    approval_tokens.create_token("todo2", "/path/b.png")

    assert approval_tokens.get_token(old_token) is None
