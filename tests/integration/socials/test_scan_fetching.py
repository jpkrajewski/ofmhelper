import pytest

from ofmhelpers.db.stores import tracked_accounts as store
from ofmhelpers.features.accounts import tracker as account_tracker
from ofmhelpers.integrations.instagram.fake import fake_instagram
from ofmhelpers.integrations.socials import client_source

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
    monkeypatch.setattr(client_source.time, "sleep", slept.append)
    return slept


def _track(*handles: str) -> list[int]:
    for handle in handles:
        store.track("instagram", handle)
    by_handle = {a.handle: a.id for a in store.list_accounts()}
    return [by_handle[h] for h in handles]


def _page_proxies(instagram, handle: str) -> list[str]:
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

    assert _page_proxies(instagram, "sheep_love33") == [P1, P2, P3]
    error = store.get_account(account).error
    assert error == "profile not found (3 tries) -- retried at the next rescan"


def test_the_error_names_the_account_not_a_busy_proxy(instagram, pauses):
    instagram.proxy_modes.update({P1: "banned", P2: "banned", P3: "rate_limited"})
    (account,) = _track("sheep_love33")

    account_tracker.scan_accounts("instagram", [account])

    error = store.get_account(account).error
    assert error == "profile not found (3 tries) -- retried at the next rescan"


def test_a_batch_stops_when_every_try_is_rate_limited(instagram, pauses):
    for proxy in (P1, P2, P3):
        instagram.proxy_modes[proxy] = "rate_limited"
    first, second = _track("sheep_love33", "brookslockvip")

    account_tracker.scan_accounts("instagram", [first, second])

    assert len(_page_proxies(instagram, "sheep_love33")) == 3
    assert _page_proxies(instagram, "brookslockvip") == []
    for account in (first, second):
        scanned = store.get_account(account)
        assert scanned.error == account_tracker.UNREACHED
        assert not scanned.syncing


def test_a_batch_stops_when_the_network_itself_is_down(instagram, pauses, monkeypatch):
    monkeypatch.setenv("OFM_IG_PROXIES", "")
    instagram.proxy_modes[""] = "timeout"
    first, second = _track("sheep_love33", "brookslockvip")

    account_tracker.scan_accounts("instagram", [first, second])

    assert len(_page_proxies(instagram, "sheep_love33")) == 1
    assert _page_proxies(instagram, "brookslockvip") == []
    assert store.get_account(first).error == account_tracker.UNREACHED
