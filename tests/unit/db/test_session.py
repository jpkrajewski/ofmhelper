# Copyright (C) 2026 jpkrajewski

import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from ofmhelpers.db import session
from ofmhelpers.db.session import get_engine


class _FakeEngine:
    def __init__(self) -> None:
        self.dispose_calls = 0

    def dispose(self) -> None:
        self.dispose_calls += 1


def test_engine_is_reused_and_disposed_when_its_url_changes(monkeypatch):
    created = []

    def make_engine(url, pool_pre_ping, future, hide_parameters):
        created.append(url)
        return _FakeEngine()

    monkeypatch.setattr(session.sqlalchemy, "create_engine", make_engine)
    monkeypatch.setattr(session.event, "listen", lambda _engine, _name, _listener: None)
    monkeypatch.setenv("OFM_DATABASE_URL", "postgresql://first")

    first = get_engine()

    assert get_engine() is first
    assert first.dispose_calls == 0

    monkeypatch.setenv("OFM_DATABASE_URL", "postgresql://second")
    second = get_engine()

    assert second is not first
    assert first.dispose_calls == 1
    assert second.dispose_calls == 0
    assert created == ["postgresql://first", "postgresql://second"]


def test_engine_is_constructed_once_for_concurrent_calls(monkeypatch):
    created = []
    listeners = []

    class SlowEngineManager(session._EngineManager):
        def __init__(self):
            super().__init__()
            time.sleep(0.02)

    def make_engine(url, pool_pre_ping, future, hide_parameters):
        created.append(url)
        time.sleep(0.02)
        return _FakeEngine()

    def listen(engine, name, listener):
        listeners.append((engine, name, listener))

    monkeypatch.setattr(session.sqlalchemy, "create_engine", make_engine)
    monkeypatch.setattr(session.event, "listen", listen)
    monkeypatch.setattr(session, "_EngineManager", SlowEngineManager)
    monkeypatch.setenv("OFM_DATABASE_URL", "postgresql://concurrent")
    session._engine_manager.cache_clear()

    with ThreadPoolExecutor(max_workers=8) as pool:
        engines = list(pool.map(lambda _: get_engine(), range(8)))

    assert created == ["postgresql://concurrent"]
    assert all(engine is engines[0] for engine in engines)
    listener_names = []
    for _, name, _ in listeners:
        listener_names.append(name)
    assert listener_names == ["begin", "before_cursor_execute"]


def test_engine_replacement_is_constructed_once_for_concurrent_calls(monkeypatch):
    created = []
    listeners = []

    def make_engine(url, pool_pre_ping, future, hide_parameters):
        created.append(url)
        if url == "postgresql://replacement":
            time.sleep(0.02)
        return _FakeEngine()

    def listen(engine, name, listener):
        listeners.append((engine, name, listener))

    monkeypatch.setattr(session.sqlalchemy, "create_engine", make_engine)
    monkeypatch.setattr(session.event, "listen", listen)
    monkeypatch.setenv("OFM_DATABASE_URL", "postgresql://replacement-original")
    first = get_engine()

    monkeypatch.setenv("OFM_DATABASE_URL", "postgresql://replacement")
    with ThreadPoolExecutor(max_workers=8) as pool:
        engines = list(pool.map(lambda _: get_engine(), range(8)))

    replacement_listeners = []
    for engine, name, _ in listeners:
        if engine is engines[0]:
            replacement_listeners.append(name)

    assert created == ["postgresql://replacement-original", "postgresql://replacement"]
    assert all(engine is engines[0] for engine in engines)
    assert engines[0] is not first
    assert first.dispose_calls == 1
    assert replacement_listeners == ["begin", "before_cursor_execute"]


def test_engine_replacement_failure_keeps_the_current_engine(monkeypatch):
    def make_engine(_url, pool_pre_ping, future, hide_parameters):
        return _FakeEngine()

    def fail_to_make_engine(_url, pool_pre_ping, future, hide_parameters):
        msg = "replacement failed"
        raise RuntimeError(msg)

    monkeypatch.setattr(session.event, "listen", lambda _engine, _name, _listener: None)
    monkeypatch.setattr(session.sqlalchemy, "create_engine", make_engine)
    monkeypatch.setenv("OFM_DATABASE_URL", "postgresql://original")
    first = get_engine()

    monkeypatch.setattr(session.sqlalchemy, "create_engine", fail_to_make_engine)
    monkeypatch.setenv("OFM_DATABASE_URL", "postgresql://unavailable")
    with pytest.raises(RuntimeError, match="replacement failed"):
        get_engine()

    assert first.dispose_calls == 0
    monkeypatch.setenv("OFM_DATABASE_URL", "postgresql://original")
    assert get_engine() is first
