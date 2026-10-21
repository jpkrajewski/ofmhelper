"""Fetching, apart from parsing: an /accounts scan batch against the
Instagram fake whose proxies fail on purpose (dead, rate-limited, banned,
timing out) -- the next proxy is tried after a jittered pause, a dead one
leaves the pool for the rest of the job, a scan gives up after its tries
and says so, a batch stops when every try is rate-limited or the network
itself is down."""

import pytest

from ofmhelpers.instagram.fake import fake_instagram
from ofmhelpers.web import account_tracker
from ofmhelpers.web.stores import tracked_accounts as store

P1, P2, P3 = (f"http://user:pw@proxy{i}.example:8000" for i in (1, 2, 3))


@pytest.fixture
def instagram(fake_externals, monkeypatch):
    monkeypatch.setenv("OFM_IG_PROXIES", f"{P1},{P2},{P3}")
    monkeypatch.setenv("OFM_ACCOUNTS_PROXY_RETRIES", "3")
    monkeypatch.setenv("OFM_ACCOUNTS_RETRY_JITTER_S", "2")
    return fake_instagram()


@pytest.fixture
def pauses(monkeypatch) -> list[float]:
    slept: list[float] = []
    monkeypatch.setattr(account_tracker.time, "sleep", slept.append)
    return slept


def _track(*handles: str) -> list[int]:
    for handle in handles:
        store.track("instagram", handle)
    by_handle = {a.handle: a.id for a in store.list_accounts()}
    return [by_handle[h] for h in handles]


def _page_proxies(instagram, handle: str) -> list[str]:
    """The proxy of every profile-page request for `handle`, in order."""
    return [proxy for _, path, proxy in instagram.calls if path == f"/{handle}/reels/"]


def test_the_next_proxy_is_tried_after_a_jittered_pause(instagram, pauses):
    instagram.proxy_modes.update({P1: "dead", P2: "rate_limited"})
    (account,) = _track("sheep_love33")

    account_tracker.scan_accounts("instagram", [account])

    assert _page_proxies(instagram, "sheep_love33") == [P1, P2, P3]
    assert len(pauses) == 2
    assert all(0 <= p <= 2 for p in pauses)
    scanned = store.get_account(account)
    assert scanned.error is None
    assert scanned.profile.followers == 12000


def test_a_dead_proxy_leaves_the_pool_for_the_rest_of_the_job(instagram, pauses):
    instagram.proxy_modes[P1] = "dead"
    accounts = _track("sheep_love33", "brookslockvip", "yuki_kanasiva")

    account_tracker.scan_accounts("instagram", accounts)

    used = [proxy for _, _, proxy in instagram.calls]
    assert used.count(P1) == 1
    assert all(store.get_account(a).error is None for a in accounts)


def test_a_scan_gives_up_after_its_tries_and_says_so(instagram, pauses):
    for proxy in (P1, P2, P3):
        instagram.proxy_modes[proxy] = "banned"
    (account,) = _track("sheep_love33")

    account_tracker.scan_accounts("instagram", [account])

    # 1 + min(3 retries, 3 proxies - 1) tries, one per proxy.
    assert _page_proxies(instagram, "sheep_love33") == [P1, P2, P3]
    error = store.get_account(account).error
    assert error == "profile not found (3 tries) -- retried at the next rescan"


def test_a_batch_stops_when_every_try_is_rate_limited(instagram, pauses):
    for proxy in (P1, P2, P3):
        instagram.proxy_modes[proxy] = "rate_limited"
    first, second = _track("sheep_love33", "brookslockvip")

    account_tracker.scan_accounts("instagram", [first, second])

    assert len(_page_proxies(instagram, "sheep_love33")) == 3
    assert _page_proxies(instagram, "brookslockvip") == []
    # Not the accounts' fault: nothing is recorded against them.
    assert store.get_account(first).error is None
    assert store.get_account(second).scanned_at is None


def test_a_batch_stops_when_the_network_itself_is_down(instagram, pauses, monkeypatch):
    monkeypatch.setenv("OFM_IG_PROXIES", "")
    instagram.proxy_modes[""] = "timeout"
    first, second = _track("sheep_love33", "brookslockvip")

    account_tracker.scan_accounts("instagram", [first, second])

    assert len(_page_proxies(instagram, "sheep_love33")) == 1
    assert _page_proxies(instagram, "brookslockvip") == []
    assert store.get_account(first).scanned_at is None
