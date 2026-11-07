"""/accounts scans through the sources in turn (integrations/socials/sources.py):
Apify first, every key most credit first, then our client logged out through
the proxies, then logged in. What one leaves unread -- keys out of credit, a
run cut short, an account the actor cannot find -- the next one reads; the
fakes answer at the wire (Apify, Instagram), nothing is paid."""

import time

import pytest

from ofmhelpers.cache.redis import get_redis
from ofmhelpers.db.stores import tracked_accounts as store
from ofmhelpers.features.accounts import tracker as account_tracker
from ofmhelpers.integrations.apify.fake import (
    BROKE_KEY,
    PROFILE_SCRAPER,
    REEL_SCRAPER,
    fake_apify,
)
from ofmhelpers.integrations.instagram.fake import fake_instagram
from ofmhelpers.integrations.socials.client_source import ProxySource
from ofmhelpers.integrations.socials.platforms import instagram_apify
from ofmhelpers.schemas import TrackedAccount

KEY_A, KEY_B = "fake-apify-a", "fake-apify-b"
PUBLIC = ("sheep_love33", "brookslockvip", "yuki_kanasiva")


@pytest.fixture
def fakes(fake_externals, monkeypatch):
    monkeypatch.setenv("APIFY_API_KEYS", KEY_A)
    monkeypatch.setenv("OFM_ACCOUNTS_RETRY_JITTER_S", "0")
    return fake_apify()


def _track(*handles: str) -> list[int]:
    for handle in handles:
        store.track("instagram", handle)
    by_handle = {a.handle: a.id for a in store.list_accounts()}
    return [by_handle[h] for h in handles]


def _scan(*handles: str) -> dict[str, TrackedAccount]:
    account_tracker.scan_accounts("instagram", _track(*handles))
    return {a.handle: a for a in store.list_accounts() if a.handle in handles}


def _source(account: TrackedAccount) -> str:
    return account.metrics.source if account.metrics else ""


def _proxy_reads() -> list[str]:
    """Handles our own client opened (logged out or in)."""
    return [
        path.strip("/").split("/")[0]
        for _, path, _ in fake_instagram().calls
        if path.endswith("/reels/")
    ]


def _runs(actor: str) -> list[dict]:
    return [i for i in fake_apify().inputs if i["actor"] == actor]


def test_apify_reads_every_account_and_only_the_last_7_days(fakes):
    accounts = _scan(*PUBLIC, "closed_doors")

    assert {h: _source(a) for h, a in accounts.items()} == dict.fromkeys(
        (*PUBLIC, "closed_doors"), "apify"
    )
    assert _proxy_reads() == []
    # One profile run for all, one reel run for the public ones, 7 days back.
    (profiles,) = _runs(PROFILE_SCRAPER)
    assert sorted(profiles["usernames"]) == sorted((*PUBLIC, "closed_doors"))
    (reels,) = _runs(REEL_SCRAPER)
    assert sorted(reels["username"]) == sorted(PUBLIC)
    assert reels["onlyPostsNewerThan"] == "7 days"
    assert not any(reels[k] for k in reels if k.startswith("include"))
    sheep = accounts["sheep_love33"]
    assert sheep.followers == 12000
    assert sheep.error is None
    # 8 reels, one 900 h old: past the 7 days, not read.
    assert len(sheep.post_views) == 7
    assert min(p.posted_at for p in sheep.post_views) > time.time() - 7 * 86400
    assert accounts["closed_doors"].profile.is_private


def test_without_apify_keys_the_proxy_client_reads(fakes, monkeypatch):
    monkeypatch.setenv("APIFY_API_KEYS", "")
    accounts = _scan(*PUBLIC)

    assert {_source(a) for a in accounts.values()} == {"proxy"}
    assert fakes.inputs == []


def test_a_key_out_of_credit_is_skipped_for_the_next(fakes, monkeypatch):
    monkeypatch.setenv("APIFY_API_KEYS", f"{BROKE_KEY},{KEY_B}")
    accounts = _scan(*PUBLIC)

    assert {_source(a) for a in accounts.values()} == {"apify"}
    assert ("POST", f"/v2/acts/{PROFILE_SCRAPER}/runs") in fakes.calls


def test_every_key_out_of_credit_falls_back_to_the_proxy_client(fakes, monkeypatch):
    monkeypatch.setenv("APIFY_API_KEYS", f"{BROKE_KEY},{BROKE_KEY}x")
    fakes.credit_left[f"{BROKE_KEY}x"] = 0.001
    accounts = _scan(*PUBLIC)

    assert {_source(a) for a in accounts.values()} == {"proxy"}
    assert fakes.inputs == []
    assert all(a.error is None for a in accounts.values())


def test_a_key_takes_only_what_its_credit_covers_the_next_key_the_rest(
    fakes, monkeypatch
):
    monkeypatch.setenv("APIFY_API_KEYS", f"{KEY_A},{KEY_B}")
    monkeypatch.setenv("OFM_ACCOUNTS_APIFY_EST_REELS", "9")
    # $0.026 an account: A (the richer) covers 2, B the third.
    fakes.credit_left[KEY_A] = 0.06
    fakes.credit_left[KEY_B] = 0.05
    accounts = _scan(*PUBLIC)

    assert {_source(a) for a in accounts.values()} == {"apify"}
    assert [len(r["usernames"]) for r in _runs(PROFILE_SCRAPER)] == [2, 1]


def test_half_a_reel_run_sends_its_accounts_on_never_half_saved(fakes, monkeypatch):
    """A reel run cut short (credit gone mid-run) keeps no account's reels:
    one read by half would save wrong views. The next key reads them; with
    none left, the proxy client."""
    fakes.cut_short.add(REEL_SCRAPER)
    accounts = _scan(*PUBLIC, "closed_doors")

    assert {h: _source(a) for h, a in accounts.items()} == {
        **dict.fromkeys(PUBLIC, "proxy"),
        "closed_doors": "apify",  # private: nothing to read past its profile
    }
    assert len(accounts["sheep_love33"].post_views) == 8


def test_an_account_apify_cannot_find_goes_to_the_proxy_not_another_key(
    fakes, monkeypatch
):
    monkeypatch.setenv("APIFY_API_KEYS", f"{KEY_A},{KEY_B}")
    accounts = _scan("sheep_love33", "no_such_account")

    assert _source(accounts["sheep_love33"]) == "apify"
    assert len(_runs(PROFILE_SCRAPER)) == 1
    assert "no_such_account" in _proxy_reads()
    assert accounts["no_such_account"].error is not None


def test_sources_follow_the_configured_order(fakes, monkeypatch):
    monkeypatch.setenv("OFM_ACCOUNTS_SOURCES", "proxy,api")
    accounts = _scan(*PUBLIC)

    assert {_source(a) for a in accounts.values()} == {"proxy"}
    assert fakes.inputs == []


class _Killed(BaseException):
    """A deploy's SIGTERM, as far as the job is concerned."""


def test_what_apify_read_is_saved_before_the_next_source_starts(fakes, monkeypatch):
    """A job killed after the paid read never pays for it again."""

    def killed(_self, _targets):
        raise _Killed

    monkeypatch.setattr(ProxySource, "scan", killed)
    with pytest.raises(_Killed):
        _scan("sheep_love33", "no_such_account")

    saved = {a.handle: a for a in store.list_accounts()}
    assert _source(saved["sheep_love33"]) == "apify"
    assert saved["sheep_love33"].followers == 12000


def test_a_failed_reel_run_keeps_the_paid_profiles_and_sends_only_public_on(
    fakes, monkeypatch
):
    monkeypatch.setenv("APIFY_API_KEYS", f"{KEY_A},{KEY_B}")
    fakes.credit_left[KEY_B] = 4.0  # A first
    fakes.cut_short.add(REEL_SCRAPER)
    accounts = _scan(*PUBLIC, "closed_doors")

    first, second = _runs(PROFILE_SCRAPER)
    assert sorted(first["usernames"]) == sorted((*PUBLIC, "closed_doors"))
    # The private one was read with A's profiles: not asked for again.
    assert sorted(second["usernames"]) == sorted(PUBLIC)
    assert _source(accounts["closed_doors"]) == "apify"
    assert {_source(accounts[h]) for h in PUBLIC} == {"proxy"}


def test_one_apify_batch_spends_at_a_time(fakes):
    """Parallel jobs sizing their share from the same credit figure would
    spend it several times over: a second batch waits, and one that cannot
    get its turn goes to the next source."""
    lock = get_redis().lock("lock:accounts-apify", timeout=60)
    assert lock.acquire(blocking=False)
    try:
        with instagram_apify._one_at_a_time(1, wait_s=0.1) as held:
            pass
    finally:
        lock.release()
    assert not held
