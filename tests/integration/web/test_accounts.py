"""/accounts: tracked social accounts with creator, notes, followers, growth
and views; scans in the background (first on add, then every 6 h), each
batch rotating through the platform's proxies."""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ.setdefault("SESSION_SECRET", "test-secret")

import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from signin import sign_in_agency

from ofmhelpers.config.settings import AccountsSettings
from ofmhelpers.socials import (
    AccountProfile,
    AccountStats,
    PostStat,
    RateLimitedError,
    RetryableError,
    SocialError,
)
from ofmhelpers.socials.platforms.instagram import InstagramPlatform
from ofmhelpers.utils.proxy_pool import ProxyPool
from ofmhelpers.web import account_tracker
from ofmhelpers.web.db.models import AccountSnapshotRow, TrackedPostRow
from ofmhelpers.web.db.repositories import (
    tracked_accounts as tracked_accounts_repository,
)
from ofmhelpers.web.db.session import session_scope
from ofmhelpers.web.main import app
from ofmhelpers.web.schemas import (
    NO_CREATOR,
    AccountHistory,
    AccountQuery,
    AccountSort,
    AccountSummary,
    AddAccounts,
)
from ofmhelpers.web.schemas.accounts import FollowerChart, Snapshot, ago, tick_label
from ofmhelpers.web.stores.tracked_accounts import (
    claim_due,
    get_account,
    get_history,
    list_accounts,
    list_creators,
    record_scan,
    set_notes,
    track,
    untrack,
)

DAY = 86400
COOKIES_TXT = (
    b"# Netscape HTTP Cookie File\n.instagram.com\tTRUE\t/\tTRUE\t0\tsessionid\tx\n"
)


@pytest.fixture(autouse=True)
def _no_retry_pause(monkeypatch):
    monkeypatch.setenv("OFM_ACCOUNTS_RETRY_JITTER_S", "0")


@pytest.fixture
def client():
    """An admin (sees the agency's accounts, which `track` makes):
    ownership is test_ownership.py's."""
    c = TestClient(app)
    sign_in_agency(c)
    return c


def _stats(handle: str, followers: int = 1000, views: int = 500) -> AccountStats:
    now = time.time()
    return AccountStats(
        profile=AccountProfile(
            handle=handle,
            display_name=handle.title(),
            followers=followers,
            following=10,
            avatar_url="https://scontent.cdninstagram.com/a.jpg",
        ),
        posts=[
            PostStat(id="new", url="https://x/new", views=views, posted_at=now - DAY),
            PostStat(id="old", url="https://x/old", views=7, posted_at=now - 60 * DAY),
        ],
        scanned_at=now,
    )


@pytest.fixture
def fetches(monkeypatch):
    """Every Instagram fetch finds `_stats(handle)`; the (handle, proxy)
    pairs fetched, in order. Enqueued jobs run inline in the test suite."""
    seen: list[tuple[str, str | None]] = []

    def fake_fetch(_self, handle, proxy, cookies=None):
        seen.append((handle, proxy))
        return _stats(handle)

    monkeypatch.setattr(InstagramPlatform, "fetch", fake_fetch)
    return seen


def _id(handle: str) -> int:
    return next(a.id for a in list_accounts() if a.handle == handle)


def _snapshot(handle: str, followers: int, age_s: float) -> None:
    """A snapshot of `handle` taken `age_s` ago."""
    with session_scope() as s:
        s.add(
            AccountSnapshotRow(
                account_id=_id(handle),
                taken_at=time.time() - age_s,
                followers=followers,
                metrics={"followers": followers},
            )
        )


# --- page -------------------------------------------------------------------


def test_page_is_in_the_nav(client):
    html = client.get("/accounts").text
    assert 'href="/accounts"' in html
    assert "No accounts yet" in html
    assert 'id="accounts-add"' in html


def test_add_tracks_and_scans(client, fetches):
    r = client.post(
        "/accounts/add",
        json={
            "platform": "instagram",
            "handles": "@Yuki_Kanasiva\nhttps://www.instagram.com/other/ — note\n\nyuki_kanasiva",
            "creator": "  Yuki   K ",
        },
    )
    assert r.status_code == 200
    assert r.json() == {"added": ["yuki_kanasiva", "other"], "existing": 0}
    assert sorted(h for h, _ in fetches) == ["other", "yuki_kanasiva"]
    accounts = {a.handle: a for a in list_accounts()}
    yuki = accounts["yuki_kanasiva"]
    assert yuki.creator == "Yuki K"
    assert yuki.status == "active"
    assert yuki.followers == 1000
    assert yuki.views(7).views == 500
    # Nothing was posted in the 7 days before: a confirmed 0, no percentage.
    assert yuki.views(7).previous == 0
    assert yuki.views(7).pct is None
    assert yuki.views(30).views == 500
    assert yuki.posts_in(7) == 1
    # One sync is no baseline: no growth yet (a dash), not "+0".
    assert yuki.growth(7) is None
    assert list_creators() == ["Yuki K"]

    html = client.get("/accounts").text
    assert "@yuki_kanasiva" in html
    assert "1,000" in html
    assert "All reachable" in html
    assert "data-problem" not in html


def test_adding_again_only_assigns_the_creator(client, fetches):
    track("instagram", "yuki")
    r = client.post(
        "/accounts/add",
        json={"platform": "instagram", "handles": "yuki", "creator": "Mia"},
    )
    assert r.json() == {"added": [], "existing": 1}
    assert get_account(_id("yuki")).creator == "Mia"


def test_one_bad_line_adds_nothing(client, fetches):
    r = client.post(
        "/accounts/add",
        json={
            "platform": "instagram",
            "handles": "good\nbad!handle extra\n",
            "creator": "",
        },
    )
    assert r.status_code == 400
    assert "bad!handle extra" in r.json()["detail"]
    assert list_accounts() == []


def test_unknown_platform_is_rejected(client):
    r = client.post("/accounts/add", json={"platform": "myspace", "handles": "tom"})
    assert r.status_code == 400
    assert "Unknown platform" in r.json()["detail"]


def test_too_many_at_once_is_rejected(client, monkeypatch):
    monkeypatch.setenv("OFM_ACCOUNTS_MAX_ADD", "2")
    r = client.post(
        "/accounts/add", json={"platform": "instagram", "handles": "a\nb\nc"}
    )
    assert r.status_code == 400
    assert list_accounts() == []


def test_creator_assign_and_unassign(client):
    track("instagram", "a")
    track("instagram", "b")
    ids = [_id("a"), _id("b")]
    r = client.post("/accounts/creator", json={"ids": ids, "creator": "Yuki"})
    assert r.json() == {"count": 2}
    assert {a.creator for a in list_accounts()} == {"Yuki"}
    client.post("/accounts/creator", json={"ids": ids[:1], "creator": ""})
    assert get_account(ids[0]).creator is None
    assert list_creators() == ["Yuki"]


def test_creator_name_cannot_look_like_the_none_filter(client):
    track("instagram", "a")
    r = client.post(
        "/accounts/creator", json={"ids": [_id("a")], "creator": NO_CREATOR}
    )
    assert r.status_code == 422


def test_notes(client):
    track("instagram", "a")
    r = client.post(f"/accounts/{_id('a')}/notes", json={"notes": "main account\nUS"})
    assert r.status_code == 200
    assert get_account(_id("a")).notes == "main account\nUS"
    assert client.post("/accounts/999999/notes", json={"notes": "x"}).status_code == 404


def test_remove_untracks_but_keeps_every_record(client, fetches):
    client.post("/accounts/add", json={"platform": "instagram", "handles": "a\nb"})
    a = _id("a")
    client.post(f"/accounts/{a}/notes", json={"notes": "banned, appealing"})
    r = client.post("/accounts/delete", json={"ids": [a]})
    assert r.json() == {"count": 1}
    assert client.post("/accounts/delete", json={"ids": [a]}).json() == {"count": 0}
    assert [x.handle for x in list_accounts()] == ["b"]
    # Not scanned while untracked.
    assert a not in claim_due(0, 0)
    # Its page, history, posts and notes are all still there.
    account = get_account(a)
    assert account.untracked_at is not None
    assert account.notes == "banned, appealing"
    assert [p.id for p in account.posts] == ["new"]  # "old" is past retention
    assert len(get_history(a).snapshots) == 1
    page = client.get(f"/accounts/{a}")
    assert page.status_code == 200
    assert "Not tracked since" in page.text


def test_adding_an_untracked_account_again_resumes_its_history(client, fetches):
    client.post("/accounts/add", json={"platform": "instagram", "handles": "a"})
    a = _id("a")
    client.post("/accounts/delete", json={"ids": [a]})
    r = client.post("/accounts/add", json={"platform": "instagram", "handles": "a"})
    assert r.json()["added"] == ["a"]  # tracked again, rescanned right away
    assert _id("a") == a  # the same account, not a new one
    assert get_account(a).untracked_at is None
    assert len(get_history(a).snapshots) == 2


def test_growth_against_the_newest_snapshot_a_period_old(fetches):
    track("instagram", "a")
    _snapshot("a", 800, 40 * DAY)
    _snapshot("a", 900, 8 * DAY)
    _snapshot("a", 950, 2 * DAY)
    record_scan(_id("a"), _stats("a", followers=1000))
    a = list_accounts()[0]
    week, month = a.growth(7), a.growth(30)
    assert (week.delta, week.full) == (100, True)
    assert week.pct == pytest.approx(100 / 9)
    assert (month.delta, month.full) == (200, True)
    assert month.pct == pytest.approx(25.0)


def test_young_account_grows_since_its_first_sync_marked_partial(fetches):
    track("instagram", "a")
    _snapshot("a", 900, 3 * DAY)
    record_scan(_id("a"), _stats("a", followers=1000))
    growth = list_accounts()[0].growth(7)
    assert (growth.delta, growth.full) == (100, False)


def test_growth_from_zero_followers_has_no_percentage(fetches):
    track("instagram", "a")
    _snapshot("a", 0, 8 * DAY)
    record_scan(_id("a"), _stats("a", followers=5))
    growth = list_accounts()[0].growth(7)
    assert (growth.delta, growth.pct) == (5, None)


def test_views_not_covered_by_a_capped_scan_are_unknown(fetches):
    track("instagram", "a")
    stats = _stats("a")
    stats.complete = False  # capped, and the oldest read post is 1 day old
    stats.posts = stats.posts[:1]
    record_scan(_id("a"), stats)
    a = list_accounts()[0]
    assert a.views(7) is None
    assert a.posts_in(7) is None


def test_failed_scan_keeps_the_numbers_and_shows_the_error(fetches):
    track("instagram", "a")
    record_scan(_id("a"), _stats("a"))
    record_scan(_id("a"), None, "Instagram changed its query")
    a = get_account(_id("a"))
    assert a.status == "error"
    assert a.followers == 1000
    assert len(get_history(a.id).snapshots) == 1


def test_private_status():
    track("instagram", "a")
    stats = _stats("a")
    stats.profile.is_private = True
    record_scan(_id("a"), stats)
    assert list_accounts()[0].status == "private"


# --- list query -------------------------------------------------------------


@pytest.fixture
def three(fetches):
    for handle, followers, views in (("aa", 10, 5), ("bb", 30, 1), ("cc", 20, 9)):
        track("instagram", handle, "Yuki" if handle != "cc" else None)
        record_scan(_id(handle), _stats(handle, followers=followers, views=views))
    _snapshot("cc", 1, 2 * DAY)


# --- rolling post storage ---------------------------------------------------


class _Clock:
    """The repository's clock, moved by hand."""

    def __init__(self) -> None:
        self.now = time.time()

    def time(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch) -> _Clock:
    fake = _Clock()
    monkeypatch.setattr(tracked_accounts_repository, "time", fake)
    return fake


def _post(code: str, age_s: float, now: float, views: int = 1) -> PostStat:
    return PostStat(
        id=code, url=f"https://x/{code}", views=views, posted_at=now - age_s
    )


def _scan(handle: str, now: float, *posts: PostStat, complete: bool = True) -> None:
    stats = _stats(handle)
    stats.posts = list(posts)
    stats.complete = complete
    stats.scanned_at = now
    record_scan(_id(handle), stats)


def _scan_as(handle: str, account_id: str, now: float, *posts: PostStat) -> None:
    """A scan of `handle` that the platform reports as `account_id`."""
    stats = _stats(handle)
    stats.profile.account_id = account_id
    stats.posts = list(posts)
    stats.scanned_at = now
    record_scan(_id(handle), stats)


def _kept(handle: str) -> dict[str, int]:
    """The account's kept posts: id -> views."""
    return {p.id: p.views for p in get_account(_id(handle)).posts}


RETENTION = 60 * DAY  # 2 x the longest default period (30 d)


def test_scan_appends_and_keeps_posts_it_did_not_return(clock):
    # Example A.
    track("instagram", "aa")
    now = clock.now
    _scan("aa", now, _post("a", 20 * DAY, now), _post("b", 29 * DAY, now))
    _scan("aa", now, _post("c", 2 * 3600, now))
    assert set(_kept("aa")) == {"a", "b", "c"}


def test_posts_past_retention_are_pruned(clock):
    # Example B, with the cutoff boundary: on it is kept, a second past is not.
    track("instagram", "aa")
    now = clock.now
    _scan(
        "aa",
        now,
        _post("old", 70 * DAY, now),
        _post("edge", RETENTION, now),
        _post("gone", RETENTION + 1, now),
    )
    assert set(_kept("aa")) == {"edge"}  # "old"/"gone" never stored
    clock.now += 1
    _scan("aa", clock.now, _post("new", 4 * 3600, clock.now))
    assert set(_kept("aa")) == {"new"}  # "edge" aged past the cutoff


def test_a_post_seen_again_is_updated_not_duplicated(clock):
    # Example C, and a retried scan.
    track("instagram", "aa")
    now = clock.now
    _scan("aa", now, _post("a", 10 * DAY, now, views=5))
    _scan("aa", now, _post("a", 10 * DAY, now, views=9), _post("b", 3600, now))
    _scan("aa", now, _post("a", 10 * DAY, now, views=9), _post("b", 3600, now))
    assert _kept("aa") == {"a": 9, "b": 1}
    with session_scope() as s:
        assert s.query(TrackedPostRow).count() == 2


def test_empty_or_failed_scan_keeps_the_posts(clock):
    # Example D.
    track("instagram", "aa")
    now = clock.now
    _scan("aa", now, _post("a", 5 * DAY, now), _post("b", 15 * DAY, now))
    _scan("aa", now)
    record_scan(_id("aa"), None, "Instagram showed no such profile (4 tries)")
    assert set(_kept("aa")) == {"a", "b"}


def test_a_kept_post_ages_out_on_the_next_successful_scan(clock):
    # Example E. A banned account's scans fail for days: its data stays as
    # its last successful sync left it, aged posts included, until a scan
    # succeeds again.
    track("instagram", "aa")
    now = clock.now
    _scan("aa", now, _post("a", RETENTION - 3600, now), _post("b", 10 * DAY, now))
    clock.now += 5 * DAY
    record_scan(_id("aa"), None, "profile not found (4 tries)")
    assert set(_kept("aa")) == {"a", "b"}
    assert get_account(_id("aa")).views(60).views == 2
    _scan("aa", clock.now, _post("c", 60, clock.now))
    assert set(_kept("aa")) == {"b", "c"}


def test_one_accounts_scan_never_touches_anothers_posts(clock):
    track("instagram", "aa")
    track("instagram", "bb")
    now = clock.now
    _scan("aa", now, _post("same", DAY, now, views=1))
    _scan("bb", now, _post("same", DAY, now, views=2))
    clock.now += RETENTION
    _scan("aa", clock.now)
    with session_scope() as s:
        rows = s.query(TrackedPostRow.account_id, TrackedPostRow.views).all()
    # aa's aged post is pruned; bb's same-id post stays until bb's own scan.
    assert rows == [(_id("bb"), 2)]
    assert _kept("bb") == {"same": 2}


def test_concurrent_scans_of_one_account_do_not_duplicate(clock):
    track("instagram", "aa")
    now = clock.now
    posts = [_post(f"p{i}", i * 3600, now, views=i) for i in range(20)]
    with ThreadPoolExecutor(4) as pool:
        list(pool.map(lambda _: _scan("aa", now, *posts), range(8)))
    assert _kept("aa") == {f"p{i}": i for i in range(20)}


def test_views_count_every_kept_post_not_only_the_latest_scan(clock):
    track("instagram", "aa")
    now = clock.now
    _scan("aa", now, _post("a", 3 * DAY, now, views=100))
    _scan("aa", now, _post("b", DAY / 2, now, views=5))
    [account] = list_accounts()
    assert account.posts == []  # the list reads views and dates only
    assert account.views(1).views == 5
    assert account.views(7).views == 105
    assert account.posts_in(7) == 2
    # No period is precomputed: any number of days works.
    assert account.views(3).previous == 0


def test_coverage_widens_from_capped_to_complete_scans(clock):
    track("instagram", "aa")
    now = clock.now
    _scan("aa", now, _post("a", DAY, now), _post("b", 3 * DAY, now), complete=False)
    account = get_account(_id("aa"))
    assert account.views(2).views == 1  # read back past 2 days
    assert account.views(2).previous is None  # but not past 4
    assert account.views(7) is None
    _scan("aa", now, _post("a", DAY, now), complete=True)
    account = get_account(_id("aa"))
    assert account.views(7).views == 2
    assert account.views(30).previous == 0


def test_a_private_account_claims_no_coverage(clock):
    track("instagram", "aa")
    stats = _stats("aa")
    stats.profile.is_private = True
    stats.posts, stats.complete = [], False
    record_scan(_id("aa"), stats)
    account = get_account(_id("aa"))
    assert account.covered_since is not None
    assert account.views(1) is None


# --- corner cases -------------------------------------------------------------


def test_a_capped_scan_after_a_gap_does_not_claim_the_gap(clock):
    # Fully read, then unreachable for 10 days; the first scan back is
    # capped and reads only 2 days: what was posted in the gap is unknown.
    track("instagram", "aa")
    now = clock.now
    _scan("aa", now, _post("a", 20 * DAY, now))
    assert get_account(_id("aa")).views(30) is not None
    clock.now += 10 * DAY
    record_scan(_id("aa"), None, "profile not found (4 tries)")
    _scan("aa", clock.now, _post("b", 2 * DAY, clock.now), complete=False)
    account = get_account(_id("aa"))
    assert account.views(1).views == 0  # within the 2 days read
    assert account.views(7) is None  # the gap was never read
    # A capped scan that does reach back to the previous one keeps the
    # coverage it had.
    clock.now += 3600
    _scan("aa", clock.now, _post("b", 2 * DAY + 3600, clock.now), complete=False)
    assert get_account(_id("aa")).views(1) is not None
    assert get_account(_id("aa")).views(7) is None


def test_coverage_never_reaches_past_retention(clock, monkeypatch):
    # With 1/7 d periods posts are kept 14 d: a later 30 d window must not
    # read as covered from posts that were pruned.
    monkeypatch.setenv("OFM_ACCOUNTS_PERIODS_DAYS", "[1, 7]")
    monkeypatch.setenv("OFM_ACCOUNTS_DEFAULT_PERIOD_DAYS", "7")
    track("instagram", "aa")
    now = clock.now
    _scan("aa", now, _post("a", DAY, now), _post("old", 20 * DAY, now))
    account = get_account(_id("aa"))
    assert account.covered_since == now - 14 * DAY
    assert account.views(7).views == 1
    assert account.views(30) is None


def test_an_account_untracked_after_its_claim_is_not_scanned(fetches):
    track("instagram", "aa")
    a = _id("aa")
    assert claim_due(0, 0) == [a]
    untrack([a])
    account_tracker.scan_accounts("instagram", [a])
    assert fetches == []


def test_a_renamed_account_merges_into_its_history(clock):
    # @old is tracked and synced, gets banned and renamed to @new; @new is
    # added: once scanned, it is the same account -- one row, both
    # histories, under the new handle.
    track("instagram", "old", "Mia")
    old_id = _id("old")
    now = clock.now
    _scan_as("old", "pk1", now, _post("a", 3 * DAY, now, views=10))
    set_notes(old_id, "main account")
    untrack([old_id])
    track("instagram", "new")
    set_notes(_id("new"), "renamed after ban")
    clock.now += 3600
    _scan_as("new", "pk1", clock.now, _post("b", 3600, clock.now, views=5))

    [account] = list_accounts()
    assert (account.id, account.handle, account.creator) == (old_id, "new", "Mia")
    assert account.untracked_at is None
    assert account.notes == "main account\nrenamed after ban"
    assert _kept("new") == {"a": 10, "b": 5}
    assert len(get_history(old_id).snapshots) == 2
    with session_scope() as s:
        assert s.query(TrackedPostRow).count() == 2


def test_failing_accounts_back_off_and_recover(clock):
    track("instagram", "aa")
    a = _id("aa")
    _scan("aa", clock.now)
    for _ in range(5):
        record_scan(a, None, "profile not found (4 tries)")
    # 5 failures: 100 s doubled 5 times, capped at 1000 s.
    clock.now += 999
    assert claim_due(100, 0, 1000) == []
    clock.now += 2
    assert claim_due(100, 0, 1000) == [a]
    # One failure: twice the interval.
    _scan("aa", clock.now)
    record_scan(a, None, "profile not found (4 tries)")
    clock.now += 150
    assert claim_due(100, 0, 1000) == []
    clock.now += 100
    assert claim_due(100, 0, 1000) == [a]
    # A success resets it.
    _scan("aa", clock.now)
    clock.now += 101
    assert claim_due(100, 0, 1000) == [a]


def _handles(client, **params) -> list[str]:
    """The rows /accounts shows for `params`, in page order."""
    html = client.get("/accounts", params=params).text
    shown = [h for h in ("aa", "bb", "cc") if f">@{h}<" in html]
    return sorted(shown, key=lambda h: html.index(f">@{h}<"))


def test_sort(client, three):
    assert _handles(client, sort="followers") == ["bb", "cc", "aa"]
    assert _handles(client, sort="views") == ["cc", "aa", "bb"]
    # Only cc has two syncs; the rest have no growth yet and go last.
    assert _handles(client, sort="growth")[0] == "cc"
    assert _handles(client, sort="newest") == ["cc", "bb", "aa"]
    assert _handles(client, sort="handle") == ["aa", "bb", "cc"]


def test_problem_rows_are_marked_and_counted(client, three):
    record_scan(_id("aa"), None, "Instagram showed no such profile (4 tries)")
    html = client.get("/accounts").text
    assert 'data-problem title="Instagram showed no such profile (4 tries)"' in html
    assert "1 unreachable" in html
    assert "badge" not in html.split('id="accounts-table"')[1]


def test_limited_syncs_warn_to_upload_a_login(client, fetches):
    track("instagram", "a")
    stats = _stats("a")
    stats.limited = True
    record_scan(_id("a"), stats)
    html = client.get("/accounts").text
    assert "1 account synced without an Instagram login" in html
    assert 'href="/cookies"' in html
    assert "without an Instagram login" in client.get(f"/accounts/{_id('a')}").text


def test_cookie_upload_rescans_limited_accounts_now(fetches):
    for handle in ("limited", "full"):
        track("instagram", handle)
        stats = _stats(handle)
        stats.limited = handle == "limited"
        record_scan(_id(handle), stats)
    fetches.clear()
    admin = TestClient(app)
    admin.post("/login", data={"password": "test-admin", "next": "/"})
    r = admin.post("/cookies", files={"file": ("cookies.txt", COOKIES_TXT)})
    assert r.status_code == 200
    assert [h for h, _ in fetches] == ["limited"]
    assert not get_account(_id("limited")).metrics.limited


def test_verified_badge_and_creator_text(client, fetches):
    track("instagram", "v", "Mia")
    stats = _stats("v")
    stats.profile.is_verified = True
    record_scan(_id("v"), stats)
    html = client.get("/accounts").text
    assert 'class="accounts-verified"' in html
    assert '<span class="accounts-creator-tag">Mia</span>' in html
    assert 'class="accounts-creator"' not in html  # not editable in the row


def test_clicking_the_current_sort_flips_its_order(client, three):
    q = AccountQuery()
    assert q.sort_link(AccountSort.FOLLOWERS) == "/accounts?order=asc"
    assert q.sort_link(AccountSort.VIEWS) == "/accounts?sort=views"
    flipped = AccountQuery(order="asc")
    assert flipped.sort_link(AccountSort.FOLLOWERS) == "/accounts?order=desc"
    assert _handles(client, order="asc") == ["aa", "cc", "bb"]
    assert _handles(client, sort="handle", order="desc") == ["cc", "bb", "aa"]
    html = client.get("/accounts", params={"order": "asc"}).text
    assert 'aria-sort="ascending"' in html


def test_filters(client, three):
    assert _handles(client, q="b") == ["bb"]
    assert _handles(client, creator="Yuki") == ["bb", "aa"]
    assert _handles(client, creator=NO_CREATOR) == ["cc"]
    assert _handles(client, status="active") == ["bb", "cc", "aa"]
    assert _handles(client, status="private") == []
    assert _handles(client, platform="instagram") == ["bb", "cc", "aa"]


def test_blank_filter_fields_mean_unset(client, three):
    """The filter form submits untouched selects as empty parameters."""
    r = client.get(
        "/accounts?q=&sort=&platform=instagram&creator=&status=&page=&per_page="
    )
    assert r.status_code == 200
    # ... and the default sort is by followers.
    assert _handles(client, q="", status="", sort="") == ["bb", "cc", "aa"]


def test_paging(client, three):
    html = client.get("/accounts", params={"per_page": 2, "page": 2}).text
    assert "Showing 3\N{EN DASH}3 of 3 accounts" in html
    # Past the last page lands on the last page.
    assert (
        "Showing 3\N{EN DASH}3"
        in client.get("/accounts", params={"per_page": 2, "page": 9}).text
    )


def test_query_links_keep_filters():
    q = AccountQuery(q="yu", creator="Mia", sort=AccountSort.VIEWS, period=30, page=3)
    assert q.link(page=4) == "/accounts?q=yu&creator=Mia&sort=views&period=30&page=4"
    assert q.link(per_page=100) == (
        "/accounts?q=yu&creator=Mia&sort=views&period=30&per_page=100"
    )
    assert q.link("/accounts/5") == "/accounts/5?q=yu&creator=Mia&sort=views&period=30"
    assert AccountQuery().link() == "/accounts"


def test_period_selector_and_summary(client, three):
    html = client.get("/accounts", params={"period": 30}).text
    assert "Views · 30d" in html
    assert 'value="30" selected' in html
    # Not a configured period: the default one.
    assert "Views · 7d" in client.get("/accounts", params={"period": 5}).text
    summary = AccountSummary.of(list_accounts(), 7)
    assert (summary.accounts, summary.followers, summary.views) == (3, 60, 15)


def test_period_selector_offers_24h_7d_14d_30d_with_7d_default(client, three):
    html = client.get("/accounts").text
    assert 'value="7" selected' in html
    for days, label in (
        (1, "Last 24 hours"),
        (14, "Last 14 days"),
        (30, "Last 30 days"),
    ):
        assert f'value="{days}"' in html
        assert label in html
    assert "Views · 24h" in client.get("/accounts", params={"period": 1}).text
    assert "Views · 14d" in client.get("/accounts", params={"period": 14}).text


def test_default_period_must_be_offered(monkeypatch):
    monkeypatch.setenv("OFM_ACCOUNTS_PERIODS_DAYS", "[1, 14]")
    with pytest.raises(ValidationError, match="DEFAULT_PERIOD_DAYS"):
        AccountsSettings()


def test_add_form_parses_handles():
    form = AddAccounts(
        platform="instagram", handles="@A\nhttps://instagram.com/b/ x\na"
    )
    assert form.parsed() == ["a", "b"]


def test_ago():
    assert [ago(t, now=100_000) for t in (100_000, 99_000, 90_000, 0)] == [
        "just now",
        "16 min ago",
        "2 h ago",
        "1 d ago",
    ]


# --- analytics ----------------------------------------------------------------


def test_analytics_page(client, fetches):
    client.post("/accounts/add", json={"platform": "instagram", "handles": "yuki"})
    _snapshot("yuki", 900, 2 * DAY)
    client.post(f"/accounts/{_id('yuki')}/notes", json={"notes": "main"})
    html = client.get(f"/accounts/{_id('yuki')}").text
    assert "@yuki" in html
    assert "1 post · last 7 days, by views" in html
    assert "https://x/new" in html
    assert "Posted 1 d ago" in html
    assert "https://x/old" not in html  # outside the period
    assert "History" not in html
    assert "data-fchart" in html
    assert "main" in html
    # A period with no posts falls back to the latest synced ones.
    track("instagram", "quiet")
    stats = _stats("quiet")
    stats.posts = stats.posts[1:]
    record_scan(_id("quiet"), stats)
    html = client.get(f"/accounts/{_id('quiet')}").text
    assert "latest synced" in html
    assert "Not enough history yet" in html
    assert client.get("/accounts/999999").status_code == 404


def test_every_post_of_the_period_is_listed_not_a_top_n(fetches):
    track("instagram", "a")
    stats = _stats("a")
    stats.posts = [
        PostStat(id=str(i), url="u", views=i, posted_at=stats.scanned_at - i * 3600)
        for i in range(15)
    ]
    record_scan(_id("a"), stats)
    top = get_account(_id("a")).top_posts(7, limit=10)
    assert top.in_period
    assert [p.id for p in top.posts] == [str(i) for i in range(14, -1, -1)]


def test_history_daily_rows_oldest_first():
    snaps = [
        {"taken_at": 0, "followers": 10, "metrics": {"followers": 10}},
        {"taken_at": 3600, "followers": 12, "metrics": {"followers": 12}},
        {"taken_at": DAY, "followers": 20, "metrics": {"followers": 20}},
    ]
    rows = AccountHistory.model_validate({"snapshots": snaps}).daily
    assert [(r.day, r.snapshot.followers, r.delta) for r in rows] == [
        ("1970-01-01", 12, None),
        ("1970-01-02", 20, 8),
    ]


# --- follower chart ------------------------------------------------------------


def _snaps(*pairs: tuple[float, int]) -> list[Snapshot]:
    """Snapshots from (taken_at, followers), oldest first."""
    return [
        Snapshot(taken_at=t, followers=f, metrics={"followers": f}) for t, f in pairs
    ]


def _history(*pairs: tuple[float, int]) -> AccountHistory:
    return AccountHistory(snapshots=_snaps(*pairs))


def test_chart_needs_two_points():
    assert not FollowerChart.of([], daily=True, gap_s=DAY).ready
    one = FollowerChart.of(_snaps((0, 5)), daily=True, gap_s=DAY)
    assert not one.ready
    assert [p.followers for p in one.points] == [5]
    assert one.lines == []


def test_chart_lays_out_points_and_rounds_the_y_range_to_nice_steps():
    chart = FollowerChart.of(
        _snaps((0, 35_287), (DAY, 36_000), (2 * DAY, 38_900)), daily=True, gap_s=2 * DAY
    )
    assert chart.ready
    assert (chart.start.followers, chart.end.followers, chart.delta) == (
        35_287,
        38_900,
        3_613,
    )
    assert round(chart.pct, 1) == 10.2
    assert [p.x for p in chart.points] == [0, 50, 100]
    assert [p.change for p in chart.points] == [None, 713, 2_900]
    # 35,287..38,900 padded to 34,750..39,250: five 1,000 gridlines.
    assert [t.label for t in chart.y_ticks] == ["35k", "36k", "37k", "38k", "39k"]
    assert [t.y for t in chart.y_ticks] == pytest.approx(
        [94.44, 72.22, 50, 27.78, 5.56], abs=0.01
    )
    # Every point sits inside the plot, the highest nearest the top.
    assert all(0 <= p.y <= 100 for p in chart.points)
    assert chart.points[2].y < chart.points[1].y < chart.points[0].y
    assert len(chart.lines) == 1
    assert chart.gaps == []
    # Dots only at the ends.
    assert [d.followers for d in chart.dots] == [35_287, 38_900]
    assert [t.x for t in chart.x_ticks] == [0, 50, 100]


def test_flat_line_sits_mid_plot():
    chart = FollowerChart.of(_snaps((0, 1000), (DAY, 1000)), daily=True, gap_s=2 * DAY)
    assert [p.y for p in chart.points] == [50, 50]
    assert [t.label for t in chart.y_ticks] == ["980", "1,000", "1,020"]
    assert chart.delta == 0
    assert chart.pct == 0
    zero = FollowerChart.of(_snaps((0, 0), (DAY, 0)), daily=True, gap_s=2 * DAY)
    assert all(0 <= p.y <= 100 for p in zero.points)  # never a range below 0
    assert zero.pct is None


def test_big_jump_keeps_every_point_in_the_plot():
    chart = FollowerChart.of(
        _snaps((0, 100), (DAY, 120), (2 * DAY, 2_500_000)), daily=True, gap_s=2 * DAY
    )
    # Scaled to the jump: 0..2.75M, the small start flat on the floor.
    assert [round(p.y) for p in chart.points] == [100, 100, 9]
    assert [t.label for t in chart.y_ticks] == ["0", "1M", "2M"]
    assert chart.points[-1].change == 2_499_880


def test_daily_chart_puts_each_day_under_its_own_label():
    """Points sit on their UTC day, not their sync time: a late-evening
    sync is not drawn next to the following day's label."""
    day0 = 300 * DAY
    chart = FollowerChart.of(
        _snaps(
            (day0 + 23 * 3600, 352),  # Oct 20-ish, 23:00
            (2 * DAY + day0 - 3 * 3600 + 3360, 600),  # next day, 20:56
            (2 * DAY + day0 + 3600, 788),  # the day after, 01:00
        ),
        daily=True,
        gap_s=1.5 * DAY,
    )
    assert [p.x for p in chart.points] == [0, 50, 100]
    assert [t.x for t in chart.x_ticks] == [0, 50, 100]
    assert [t.taken_at // DAY for t in chart.x_ticks] == [300, 301, 302]
    assert chart.span_days == 2
    assert chart.gaps == []
    # The y-range hugs the data: 300..850, gridlines at 400/600/800.
    assert [t.label for t in chart.y_ticks] == ["400", "600", "800"]
    assert all(0 < p.y < 100 for p in chart.points)


def test_long_daily_chart_labels_at_most_eight_days_ending_on_the_last():
    chart = FollowerChart.of(
        _snaps(*((d * DAY, 1000 + d) for d in range(30))), daily=True, gap_s=1.5 * DAY
    )
    assert len(chart.x_ticks) == 8
    assert chart.x_ticks[-1].x == 100
    assert 3 <= len(chart.y_ticks) <= 5


def test_missed_syncs_break_the_line_with_a_dashed_gap():
    chart = FollowerChart.of(
        _snaps((0, 10), (DAY, 11), (5 * DAY, 15), (6 * DAY, 16), (10 * DAY, 20)),
        daily=True,
        gap_s=1.5 * DAY,
    )
    assert len(chart.lines) == 2
    assert len(chart.gaps) == 2
    # The last point is alone after its gap: it gets a dot (and is the end).
    assert [d.followers for d in chart.dots] == [10, 20]
    lone = FollowerChart.of(
        _snaps((0, 10), (5 * DAY, 15), (10 * DAY, 20)), daily=True, gap_s=1.5 * DAY
    )
    assert lone.lines == []
    assert [d.followers for d in lone.dots] == [10, 15, 20]


def test_tick_labels_carry_the_decimals_the_step_needs():
    assert tick_label(9_500, 500, 9_500) == "9,500"
    assert tick_label(38_850, 50, 39_000) == "38.85k"
    assert tick_label(38_800, 200, 39_000) == "38.8k"
    assert tick_label(1_200_000, 100_000, 1_500_000) == "1.2M"
    assert tick_label(2_000_000, 1_000_000, 3_000_000) == "2M"


def test_history_chart_starts_at_the_growth_baseline_and_is_daily_past_two_days():
    now = 100 * DAY
    history = _history(
        (now - 9 * DAY, 1),  # older than the baseline: left out
        (now - 8 * DAY, 100),  # newest a full period old: the baseline
        (now - 3 * DAY, 110),
        (now - 3 * DAY + 3600, 120),  # same UTC day: the day's last counts
        (now - 1, 150),
    )
    chart = history.follower_chart(7, rescan_s=6 * 3600, now=now)
    assert chart.daily
    assert [p.followers for p in chart.points] == [100, 120, 150]
    # UTC days 92, 97, 99: placed by day, not by sync time.
    assert [p.x for p in chart.points] == pytest.approx([0, 500 / 7, 100])
    assert chart.delta == 50
    # 24 hours: every sync, no daily folding.
    short = history.follower_chart(1, rescan_s=6 * 3600, now=now)
    assert not short.daily
    assert [p.followers for p in short.points] == [120, 150]
    assert len(short.gaps) == 1  # three days apart, rescans every 6 h


def test_chart_delta_matches_the_growth_kpi(client, fetches):
    client.post("/accounts/add", json={"platform": "instagram", "handles": "yuki"})
    _snapshot("yuki", 900, 8 * DAY)
    _snapshot("yuki", 950, 3 * DAY)
    account = get_account(_id("yuki"))
    chart = get_history(_id("yuki")).follower_chart(7, rescan_s=6 * 3600)
    assert chart.delta == account.growth(7).delta == 100


def test_analytics_page_draws_the_follower_chart(client, fetches):
    client.post("/accounts/add", json={"platform": "instagram", "handles": "yuki"})
    _snapshot("yuki", 900, 8 * DAY)
    _snapshot("yuki", 950, 2 * DAY)
    _snapshot("yuki", 980, DAY)
    html = client.get(f"/accounts/{_id('yuki')}").text
    assert "data-fchart" in html
    assert 'data-step="day"' in html
    assert 'class="fchart-line"' in html
    # Start, now and change, and a data table row per point.
    assert ">900<" in html
    assert ">1,000<" in html
    assert "+100" in html
    assert "(+11.1%)" in html
    assert html.count("<tr data-x=") == 4
    assert "<small>8 days</small>" in html  # first point to last, not the count
    assert ">+50<" in html  # 900 -> 950
    assert "Not enough history yet" not in html
    assert 'src="/static/js/account-chart.js' in html
    assert 'class="fchart-gap"' in html  # nothing between 8 and 2 days ago
    # 24 hours: every sync, from the baseline a day back.
    html = client.get(f"/accounts/{_id('yuki')}", params={"period": 1}).text
    assert 'data-step="sync"' in html
    assert html.count("<tr data-x=") == 2
    assert "Dashed: no syncs in between." in html  # 24 h apart, rescans every 6 h


def test_analytics_page_without_enough_history_says_so(client):
    track("instagram", "fresh")
    html = client.get(f"/accounts/{_id('fresh')}").text
    assert "Not enough history yet: the chart needs syncs on two different days" in html
    assert "Tracking since" in html
    assert "data-fchart" not in html
    record_scan(_id("fresh"), _stats("fresh"))
    html = client.get(f"/accounts/{_id('fresh')}", params={"period": 1}).text
    assert "Not enough history yet: the chart needs two syncs" in html
    assert "One so far: 1,000 followers" in html
    assert "data-fchart" not in html


# --- tracker ----------------------------------------------------------------


def test_due_accounts_are_claimed_once_and_rescanned_after_the_interval(
    fetches, monkeypatch
):
    track("instagram", "a")
    assert claim_due(6 * 3600, 3600) == [_id("a")]
    assert claim_due(6 * 3600, 3600) == []
    record_scan(_id("a"), _stats("a"))
    assert claim_due(6 * 3600, 3600) == []
    assert claim_due(0, 3600) == [_id("a")]


def test_sweep_queues_one_job_per_batch_with_staggered_proxies(monkeypatch):
    monkeypatch.setenv("OFM_ACCOUNTS_BATCH_SIZE", "2")
    for h in "abcde":
        track("instagram", h)
    jobs = []
    monkeypatch.setattr(account_tracker, "enqueue", lambda fn, *args: jobs.append(args))
    due = account_tracker.queue_due_scans()
    assert len(due) == 5
    assert [(p, len(ids), start) for p, ids, start in jobs] == [
        ("instagram", 2, 0),
        ("instagram", 2, 1),
        ("instagram", 1, 2),
    ]


def _failing(monkeypatch, outcomes):
    """Each fetch takes the next outcome: an exception is raised, anything
    else is a successful scan. Returns the proxies used."""
    used: list[str | None] = []
    it = iter(outcomes)

    def fetch(_self, handle, proxy, cookies=None):
        used.append(proxy)
        outcome = next(it)
        if isinstance(outcome, BaseException):
            raise outcome
        return _stats(handle)

    monkeypatch.setattr(InstagramPlatform, "fetch", fetch)
    return used


def test_retries_on_the_next_proxy(monkeypatch):
    monkeypatch.setenv("OFM_IG_PROXIES", "a:1,b:2,c:3")
    track("instagram", "x")
    used = _failing(monkeypatch, [RetryableError("502"), "ok"])
    account_tracker.scan_accounts("instagram", [_id("x")])
    assert used == ["http://a:1", "http://b:2"]
    assert get_account(_id("x")).status == "active"


def test_every_try_answered_records_the_error(monkeypatch):
    monkeypatch.setenv("OFM_IG_PROXIES", "a:1,b:2")
    track("instagram", "x")
    _failing(monkeypatch, [RetryableError("profile not found")] * 2)
    account_tracker.scan_accounts("instagram", [_id("x")])
    assert "profile not found (2 tries)" in get_account(_id("x")).error


def test_lasting_error_is_recorded_without_retry(monkeypatch):
    monkeypatch.setenv("OFM_IG_PROXIES", "a:1,b:2")
    track("instagram", "x")
    used = _failing(monkeypatch, [SocialError("Instagram changed its query")])
    account_tracker.scan_accounts("instagram", [_id("x")])
    assert len(used) == 1
    assert get_account(_id("x")).error == "Instagram changed its query"


def test_rate_limit_on_every_try_stops_the_batch(monkeypatch):
    monkeypatch.setenv("OFM_IG_PROXIES", "a:1,b:2")
    for h in ("x", "y"):
        track("instagram", h)
    used = _failing(monkeypatch, [RateLimitedError("429")] * 2)
    account_tracker.scan_accounts("instagram", [_id("x"), _id("y")])
    assert len(used) == 2
    # Nothing recorded: both keep their claim and retry later.
    assert all(a.status == "scanning" and a.error is None for a in list_accounts())


def test_never_reached_records_nothing_and_dead_proxies_leave(monkeypatch):
    monkeypatch.setenv("OFM_IG_PROXIES", "a:1,b:2,c:3")
    monkeypatch.setenv("OFM_ACCOUNTS_PROXY_RETRIES", "1")
    for h in ("x", "y"):
        track("instagram", h)
    dead = RetryableError("proxy", reached=False, dead_proxy=True)
    used = _failing(monkeypatch, [dead, dead, "ok"])
    account_tracker.scan_accounts("instagram", [_id("x"), _id("y")])
    assert used == ["http://a:1", "http://b:2", "http://c:3"]
    assert get_account(_id("x")).error is None
    assert get_account(_id("y")).status == "active"


def test_direct_connection_failing_stops_the_batch(monkeypatch):
    for h in ("x", "y"):
        track("instagram", h)
    used = _failing(monkeypatch, [RetryableError("dns", reached=False)])
    account_tracker.scan_accounts("instagram", [_id("x"), _id("y")])
    assert used == [None]


def test_removed_account_is_skipped(fetches):
    account_tracker.scan_accounts("instagram", [123456])
    assert fetches == []


def test_proxy_pool_starts_where_told():
    pool = ProxyPool(["a", "b", "c"], start=4)
    assert [pool.next() for _ in range(3)] == ["b", "c", "a"]
    assert ProxyPool([], start=3).next() is None
