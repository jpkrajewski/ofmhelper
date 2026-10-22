"""/cookies: each user uploads, sees, replaces and removes only their own
cookies.txt (encrypted in Postgres); their downloads and their accounts'
scans log in with it, nobody else's; a watched profile's one shared scan
uses the earliest-added watcher's who has any. No value ever reaches a
page, a log line or Redis."""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ.setdefault("SESSION_SECRET", "test-secret")

import logging
import time

import pytest
from fastapi.testclient import TestClient
from signin import sign_in
from sqlalchemy import text

from ofmhelpers.cache import get_redis
from ofmhelpers.socials import AccountProfile, AccountStats
from ofmhelpers.socials.platforms.instagram import InstagramPlatform
from ofmhelpers.web import account_tracker, reel_watch
from ofmhelpers.web.db.session import session_scope
from ofmhelpers.web.main import app
from ofmhelpers.web.routers.downloads import media as download_media
from ofmhelpers.web.stores import cookies
from ofmhelpers.web.stores.tracked_accounts import (
    get_account,
    list_accounts,
    record_scan,
    track,
)
from ofmhelpers.web.stores.watched_profiles import watch


def _jar(secret: str, domain: str = ".instagram.com") -> bytes:
    return (
        "# Netscape HTTP Cookie File\n"
        f"{domain}\tTRUE\t/\tTRUE\t1893456000\tsessionid\t{secret}\n"
        f".tiktok.com\tTRUE\t/\tTRUE\t0\tsid_tt\t{secret}-tt\n"
    ).encode()


ANN_SECRET = "ann-session-value-123"
BOB_SECRET = "bob-session-value-456"


def _client(email: str) -> tuple[TestClient, int]:
    c = TestClient(app)
    return c, sign_in(c, email).id


@pytest.fixture
def ann() -> tuple[TestClient, int]:
    return _client("ann@example.com")


@pytest.fixture
def bob() -> tuple[TestClient, int]:
    return _client("bob@example.com")


def _upload(client: TestClient, payload: bytes, name: str = "cookies.txt"):
    return client.post("/cookies", files={"file": (name, payload)})


def test_upload_shows_what_it_covers_never_a_value(ann):
    client, _ = ann
    r = _upload(client, _jar(ANN_SECRET))
    assert r.status_code == 200
    html = r.text
    assert "Cookies saved." in html
    assert 'data-cookies="saved"' in html
    assert "2 cookies" in html
    assert "instagram.com, tiktok.com" in html
    assert "Instagram login</strong> · yes, expires 2030-01-01" in html
    assert ANN_SECRET not in html
    assert r.headers["cache-control"] == "no-store"


def test_one_users_cookies_are_never_shown_to_another(ann, bob):
    _upload(ann[0], _jar(ANN_SECRET), "ann-export.txt")
    html = bob[0].get("/cookies").text
    assert 'data-cookies="none"' in html
    assert "ann-export.txt" not in html
    assert ANN_SECRET not in html
    assert cookies.for_user(bob[1]) is None


def test_replace_then_remove(ann):
    client, user_id = ann
    _upload(client, _jar(ANN_SECRET))
    _upload(client, _jar("newer", ".youtube.com"), "second.txt")
    saved = cookies.saved(user_id)
    assert saved is not None
    assert saved.file_name == "second.txt"
    assert saved.summary.domains == ["tiktok.com", "youtube.com"]
    assert not saved.summary.instagram_login
    assert ANN_SECRET not in (cookies.for_user(user_id) or "")

    r = client.post("/cookies/delete")
    assert r.status_code == 200
    assert "Cookies removed." in r.text
    assert 'data-cookies="none"' in r.text
    assert cookies.for_user(user_id) is None


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        (b"sessionid=abc; csrftoken=def", "Not a cookies.txt file"),
        (b"# Netscape HTTP Cookie File\n", "holds no cookies"),
        (b"\xff\xfe\x00", "Not a cookies.txt file"),
    ],
)
def test_a_file_that_is_not_cookies_txt_is_refused_and_keeps_the_old(
    ann, payload, reason
):
    client, user_id = ann
    _upload(client, _jar(ANN_SECRET))
    r = _upload(client, payload)
    assert r.status_code == 400
    assert reason in r.text
    assert ANN_SECRET in (cookies.for_user(user_id) or "")


def test_a_too_big_file_is_refused(ann, monkeypatch):
    monkeypatch.setenv("OFM_COOKIES_MAX_BYTES", "10")
    r = _upload(ann[0], _jar(ANN_SECRET))
    assert r.status_code == 400
    assert "too big" in r.text
    assert cookies.for_user(ann[1]) is None


def test_stored_encrypted_and_never_in_redis_or_logs(ann, caplog):
    client, user_id = ann
    caplog.set_level(logging.DEBUG)
    _upload(client, _jar(ANN_SECRET))
    client.get("/cookies")
    with session_scope() as s:
        raw = s.execute(
            text("SELECT cookies FROM user_cookies WHERE user_id = :u"), {"u": user_id}
        ).scalar_one()
    assert ANN_SECRET not in raw
    assert ANN_SECRET in (cookies.for_user(user_id) or "")
    redis = get_redis()
    for key in redis.scan_iter():
        if redis.type(key) in (b"string", "string"):
            value = redis.get(key) or b""
            text_value = (
                value.decode(errors="replace") if isinstance(value, bytes) else value
            )
            assert ANN_SECRET not in text_value
    assert ANN_SECRET not in caplog.text


@pytest.fixture
def fetched(monkeypatch) -> dict[str, str | None]:
    """handle -> the cookies its scan logged in with."""
    seen: dict[str, str | None] = {}

    def fetch(_self, handle, proxy, cookies=None):
        seen[handle] = cookies
        return AccountStats(
            profile=AccountProfile(handle=handle, followers=10),
            posts=[],
            scanned_at=time.time(),
        )

    monkeypatch.setattr(InstagramPlatform, "fetch", fetch)
    return seen


def _id(handle: str, owner: int) -> int:
    return next(a.id for a in list_accounts(owner) if a.handle == handle)


def test_each_accounts_scan_logs_in_as_its_owner_only(ann, bob, fetched):
    _upload(ann[0], _jar(ANN_SECRET))
    track("instagram", "anns_account", user_id=ann[1])
    track("instagram", "bobs_account", user_id=bob[1])
    fetched.clear()

    account_tracker.scan_accounts(
        "instagram", [_id("anns_account", ann[1]), _id("bobs_account", bob[1])]
    )

    assert ANN_SECRET in (fetched["anns_account"] or "")
    assert fetched["bobs_account"] is None


def test_an_upload_resyncs_only_the_uploaders_limited_accounts(ann, bob, fetched):
    for handle, owner in (("anns", ann[1]), ("bobs", bob[1])):
        track("instagram", handle, user_id=owner)
        stats = AccountStats(
            profile=AccountProfile(handle=handle, followers=1),
            posts=[],
            scanned_at=time.time(),
            limited=True,
        )
        record_scan(_id(handle, owner), stats)
    fetched.clear()

    _upload(ann[0], _jar(ANN_SECRET))

    assert list(fetched) == ["anns"]
    assert ANN_SECRET in (fetched["anns"] or "")
    assert not get_account(_id("anns", ann[1])).metrics.limited
    assert get_account(_id("bobs", bob[1])).metrics.limited
    # Bob's banner still asks him for his own login; Ann's is gone.
    assert "without an Instagram login" in bob[0].get("/accounts").text
    assert "without an Instagram login" not in ann[0].get("/accounts").text


def test_a_watched_profiles_shared_scan_uses_the_earliest_watcher_with_cookies(
    ann, bob, monkeypatch
):
    used: list[str | None] = []

    def scan(client, *_args):
        used.append(client._cookies)
        msg = "stop here"
        raise reel_watch.InstagramError(msg)

    monkeypatch.setattr(reel_watch, "scan_profile", scan)
    watch("shared", user_id=bob[1])  # Bob first, no cookies yet
    watch("shared", user_id=ann[1])
    watch("anns_only", user_id=ann[1])

    assert cookies.for_watched("shared") is None
    _upload(ann[0], _jar(ANN_SECRET))
    reel_watch.scan_watched("shared")
    assert ANN_SECRET in (used[-1] or "")

    _upload(bob[0], _jar(BOB_SECRET))
    reel_watch.scan_watched("shared")
    assert BOB_SECRET in (used[-1] or "")
    # A profile only Ann watches never gets Bob's login.
    assert ANN_SECRET in (cookies.for_watched("anns_only") or "")
    assert cookies.for_watched("nobody_watches") is None


def test_a_download_logs_in_as_whoever_started_it(ann, bob, monkeypatch):
    logins: list[str | None] = []

    def download_all(urls, cookies=None):
        logins.append(cookies)
        return []

    monkeypatch.setattr(download_media, "download_all", download_all)
    _upload(ann[0], _jar(ANN_SECRET))

    for client, _ in (ann, bob):
        r = client.post("/download-media/run", data={"urls": "https://x.test/p/1"})
        assert r.status_code == 200

    assert ANN_SECRET in (logins[0] or "")
    assert logins[1] is None
