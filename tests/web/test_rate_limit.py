"""
web/ratelimit.py: the failed-login brake on /login (two shared passwords, no
per-user lockout, so this is the only thing bounding a guessing attack) and
the blunt per-IP ceiling on mutating requests.

Rate limiting is off for the rest of the suite (see conftest) because every
test shares one client host; these tests turn it back on explicitly.
"""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ["APP_PASSWORD_VA"] = "test-va"
os.environ.setdefault("SESSION_SECRET", "test-secret")

import pytest
from fastapi.testclient import TestClient
from redis.exceptions import RedisError

from ofmhelpers.web.main import app


@pytest.fixture
def limited(monkeypatch):
    monkeypatch.setenv("OFM_RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("OFM_LOGIN_MAX_FAILURES", "3")
    monkeypatch.setenv("OFM_LOGIN_FAILURE_WINDOW_S", "900")
    # High enough to stay out of the login tests' way -- the write ceiling
    # counts POST /login too. The write test lowers it itself.
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
    """Once the allowance is gone the endpoint stops answering at all --
    otherwise an attacker who guessed right on attempt N+1 would still get in."""
    for _ in range(3):
        _guess(limited)

    r = _guess(limited, password="test-admin")
    assert r.status_code == 429


def test_successful_login_clears_the_failure_counter(limited):
    _guess(limited)
    _guess(limited)

    r = _guess(limited, password="test-admin")
    assert r.status_code == 303

    # Counter reset: a fresh run of failures gets the full allowance again.
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

    # The login POST above already counted; drive the rest of the allowance.
    statuses = [
        limited.post("/todo/nope/toggle", follow_redirects=False).status_code
        for _ in range(6)
    ]
    assert 429 in statuses
    assert statuses[-1] == 429

    # GETs are never counted, so the app stays usable for reading.
    assert limited.get("/health").status_code == 200


def test_a_failed_expire_cannot_lock_an_ip_out_forever(limited, monkeypatch):
    """INCR landing while EXPIRE fails must not leave a TTL-less counter: that
    key would never expire and the IP could never log in again."""
    from ofmhelpers.web import ratelimit
    from ofmhelpers.web.queue import get_redis

    key = ratelimit._key(ratelimit._LOGIN_BUCKET, "testclient")
    real_expire = get_redis().expire
    failed_once = {"done": False}

    def flaky_expire(name, *args, **kwargs):
        # Only the login counter, and only its first hit -- the same request
        # also bumps the write-ceiling counter through this same client.
        if name == key and not failed_once["done"]:
            failed_once["done"] = True
            msg = "simulated broker blip"
            raise RedisError(msg)
        return real_expire(name, *args, **kwargs)

    monkeypatch.setattr(get_redis(), "expire", flaky_expire)

    _guess(limited)  # INCR ok, EXPIRE raises -> counter has no TTL
    assert get_redis().ttl(key) == -1  # -1 = key exists, no expiry

    _guess(limited)  # the next hit must repair it
    assert get_redis().ttl(key) > 0
