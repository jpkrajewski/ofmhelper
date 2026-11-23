# Copyright (C) 2026 jpkrajewski

import pytest
from fastapi.testclient import TestClient
from signin import sign_in_agency

from ofmhelpers.db.tracked_accounts import store
from ofmhelpers.features.accounts import tracker as account_tracker
from ofmhelpers.integrations.snapchat import fake as snapchat_fake
from ofmhelpers.web.main import app


@pytest.fixture(autouse=True)
def _no_retry_pause(monkeypatch):
    monkeypatch.setenv("OFM_ACCOUNTS_RETRY_JITTER_S", "0")


@pytest.fixture
def client(fake_externals):
    c = TestClient(app)
    sign_in_agency(c)
    return c


def _ids(platform: str, *handles: str) -> list[int]:
    for handle in handles:
        store.track(platform, handle)
    by_handle = {(a.platform, a.handle): a.id for a in store.list_accounts()}
    return [by_handle[platform, h] for h in handles]


def test_a_scan_saves_followers_and_views_of_snapchat_and_x(fake_externals):
    snap, hidden, gone = _ids("snapchat", "fake_snap", "fake_hidden", "nobody_snaps")
    (x,) = _ids("x", "fake_x")

    account_tracker.scan_accounts("snapchat", [snap, hidden, gone])
    account_tracker.scan_accounts("x", [x])

    snapped = store.get_account(snap)
    assert snapped.error is None
    assert snapped.followers == 12_400
    assert snapped.metrics.source == "proxy"
    assert snapped.views(7).views == 5200 + 880
    assert snapped.views(30).views == 5200 + 880 + 31_000
    assert snapped.hidden_posts == 1
    assert not snapped.followers_hidden
    assert "not a public profile" in store.get_account(hidden).error
    assert "is not on Snapchat" in store.get_account(gone).error

    posted = store.get_account(x)
    assert posted.error is None
    assert posted.followers == 8_300
    assert posted.views(1).views == 12_000
    assert posted.views(7).views == 12_000 + 640
    assert posted.views(30) is None
    assert posted.profile_url == "https://x.com/fake_x"


def test_every_account_carries_its_profile_link_and_platform_name(fake_externals):
    (insta,) = _ids("instagram", "sheep_love33")
    (snap,) = _ids("snapchat", "fake_snap")

    listed = {a.id: a for a in store.list_accounts()}
    expected = [
        ("Instagram", "https://www.instagram.com/sheep_love33/"),
        ("Snapchat", "https://www.snapchat.com/@fake_snap"),
    ]

    read = [store.get_account(insta), store.get_account(snap)]

    assert [(a.platform_label, a.profile_url) for a in read] == expected
    assert [
        (listed[i].platform_label, listed[i].profile_url) for i in (insta, snap)
    ] == expected


def test_the_page_shows_one_platform_at_a_time_instagram_first(client):
    _ids("instagram", "sheep_love33")
    (snap,) = _ids("snapchat", "fake_snap")
    (x,) = _ids("x", "fake_x")
    account_tracker.scan_accounts("snapchat", [snap])
    account_tracker.scan_accounts("x", [x])

    first = client.get("/accounts").text
    assert "@sheep_love33" in first
    assert "@fake_snap" not in first
    assert '<option value="instagram" selected>' in first

    snapchat = client.get("/accounts?platform=snapchat").text
    assert "@fake_snap" in snapchat
    assert "@sheep_love33" not in snapchat
    assert "12,400" in snapchat
    assert '<option value="snapchat" selected>' in snapchat

    shown = client.get("/accounts?platform=x").text
    assert "@fake_x" in shown
    assert "@fake_snap" not in shown
    assert "8.3k" in shown


def test_a_hidden_follower_count_and_hidden_spotlights_read_as_hidden(
    client,
    monkeypatch,
):
    quiet = snapchat_fake.PROFILES[0].model_copy(update={"subscribers": 0})
    monkeypatch.setattr(snapchat_fake, "PROFILES", (quiet,))
    (snap,) = _ids("snapchat", "fake_snap")
    account_tracker.scan_accounts("snapchat", [snap])

    account = store.get_account(snap)
    assert account.followers is None
    assert account.growth(7) is None
    assert account.views(7).views == 5200 + 880
    [listed_account] = store.list_accounts()
    assert listed_account.views(7).views == 5200 + 880
    listed = client.get("/accounts?platform=snapchat").text
    assert "6.1k" in listed
    assert "Hidden</span>" in listed
    assert "1 hidden by Snapchat" in listed
    assert "Across 0 accounts" not in listed
    assert "Hidden by Snapchat" in listed
    detail = client.get(f"/accounts/{snap}?platform=snapchat").text
    assert "Snapchat hides this account's follower count." in detail
    assert "Hidden by Snapchat" in detail
    assert store.get_history(snap).snapshots == []


def test_a_count_shown_after_being_hidden_grows_from_itself_not_zero(
    fake_externals,
    monkeypatch,
):
    original = snapchat_fake.PROFILES[0]
    quiet = original.model_copy(update={"subscribers": 0})
    monkeypatch.setattr(snapchat_fake, "PROFILES", (quiet,))
    (snap,) = _ids("snapchat", "fake_snap")
    account_tracker.scan_accounts("snapchat", [snap])
    store.mark_due([snap])
    monkeypatch.setattr(snapchat_fake, "PROFILES", (original,))
    account_tracker.scan_accounts("snapchat", [snap])

    account = store.get_account(snap)
    assert account.followers == 12_400
    assert account.growth(7) is None
    assert [s.followers for s in store.get_history(snap).snapshots] == [12_400]


def test_an_unknown_platform_shows_an_empty_page(client):
    assert client.get("/accounts?platform=bogus").status_code == 200


def test_a_hidden_count_leaves_the_shown_ones_in_the_total(client, monkeypatch):
    quiet = snapchat_fake.PROFILES[0].model_copy(
        update={"username": "quiet_snap", "subscribers": 0},
    )
    monkeypatch.setattr(snapchat_fake, "PROFILES", (snapchat_fake.PROFILES[0], quiet))
    ids = _ids("snapchat", "fake_snap", "quiet_snap")
    account_tracker.scan_accounts("snapchat", ids)

    listed = client.get("/accounts?platform=snapchat").text
    assert "12k" in listed
    assert "Across 1 account · 1 hidden by Snapchat" in listed


def test_an_empty_platform_says_which(client):
    html = client.get("/accounts?platform=x").text
    assert "No X accounts yet" in html


def test_adding_a_snapchat_link_tracks_and_scans_it(client):
    r = client.post(
        "/accounts/add",
        json={
            "platform": "snapchat",
            "handles": "https://www.snapchat.com/add/Fake_Snap",
            "creator": "",
        },
    )
    assert r.status_code == 200
    assert r.json() == {"added": ["fake_snap"], "existing": 0}
    [account] = [a for a in store.list_accounts() if a.platform == "snapchat"]
    assert account.handle == "fake_snap"


def test_a_junk_x_handle_adds_nothing(client):
    r = client.post(
        "/accounts/add",
        json={"platform": "x", "handles": "fake_x\ndane.walker", "creator": ""},
    )
    assert r.status_code == 400
    assert r.json()["detail"] == "Not a valid X account: dane.walker"
    assert not [a for a in store.list_accounts() if a.platform == "x"]
