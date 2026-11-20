import pytest
from fastapi.testclient import TestClient
from redis.exceptions import RedisError

from ofmhelpers.web.main import app


@pytest.fixture
def limited(monkeypatch):
    monkeypatch.setenv("OFM_RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("OFM_LOGIN_MAX_FAILURES", "3")
    monkeypatch.setenv("OFM_LOGIN_FAILURE_WINDOW_S", "900")
    monkeypatch.setenv("OFM_WRITE_RATE_LIMIT_REQUESTS", "100")
    monkeypatch.setenv("OFM_WRITE_RATE_LIMIT_WINDOW_S", "60")
    return TestClient(app)


def _guess(client, password="wrong"):
    return client.post(
        "/login", data={"password": password, "next": "/"}, follow_redirects=False
    )


def test_login_locks_out_after_the_failure_allowance(limited):
    for _ in range(3):
        assert _guess(limited).status_code == 401

    r = _guess(limited)
    assert r.status_code == 429
    assert "Too many failed attempts" in r.text


def test_lockout_applies_even_to_the_correct_password(limited):
    for _ in range(3):
        _guess(limited)

    r = _guess(limited, password="test-admin")
    assert r.status_code == 429


def test_successful_login_clears_the_failure_counter(limited):
    _guess(limited)
    _guess(limited)

    r = _guess(limited, password="test-admin")
    assert r.status_code == 303

    for _ in range(3):
        assert _guess(limited).status_code == 401


def test_login_is_unlimited_when_rate_limiting_is_off(monkeypatch):
    monkeypatch.setenv("OFM_RATE_LIMIT_ENABLED", "false")
    client = TestClient(app)

    for _ in range(12):
        assert _guess(client).status_code == 401


def test_write_ceiling_returns_429_and_leaves_reads_alone(limited, monkeypatch):
    monkeypatch.setenv("OFM_WRITE_RATE_LIMIT_REQUESTS", "5")
    limited.post("/login", data={"password": "test-admin", "next": "/"})

    statuses = [
        limited.post("/todo/nope/toggle", follow_redirects=False).status_code
        for _ in range(6)
    ]
    assert 429 in statuses
    assert statuses[-1] == 429

    assert limited.get("/health").status_code == 200


def test_a_failed_expire_cannot_lock_an_ip_out_forever(limited, monkeypatch):
    from ofmhelpers.cache import get_redis
    from ofmhelpers.web.middleware import ratelimit

    key = ratelimit._key(ratelimit._LOGIN_BUCKET, "testclient")
    real_expire = get_redis().expire
    failed_once = {"done": False}

    def flaky_expire(name, *args, **kwargs):
        if name == key and not failed_once["done"]:
            failed_once["done"] = True
            msg = "simulated broker blip"
            raise RedisError(msg)
        return real_expire(name, *args, **kwargs)

    monkeypatch.setattr(get_redis(), "expire", flaky_expire)

    _guess(limited)
    assert get_redis().ttl(key) == -1

    _guess(limited)
    assert get_redis().ttl(key) > 0
