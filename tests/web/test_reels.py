"""/reels: a watch list of Instagram profiles in groups, one paged feed of
their viral reels, scans in the background (first on add, then every 12 h),
and the picked reels handed to /clone."""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ["APP_PASSWORD_VA"] = "test-va"
os.environ.setdefault("SESSION_SECRET", "test-secret")

import io
import json
import zipfile
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
import requests
from fastapi.testclient import TestClient

from ofmhelpers.instagram import (
    InstagramError,
    InstagramRateLimitedError,
    InstagramUnavailableError,
    ProfileNotFoundError,
    ProfilePrivateError,
)
from ofmhelpers.instagram.models import (
    Profile,
    ProfileReels,
    Reel,
    ReelMedia,
    ReelStat,
)
from ofmhelpers.utils.proxy_pool import ProxyPool
from ofmhelpers.web import reel_watch
from ofmhelpers.web.main import app
from ofmhelpers.web.routers.downloads import reels
from ofmhelpers.web.routers.task_helpers import uploads as task_helper_uploads
from ofmhelpers.web.schemas import FeedItem, Leaderboard, WatchedProfile, WatchForm
from ofmhelpers.web.stores.watched_profiles import (
    claim_due,
    get_watched,
    list_groups,
    list_watched,
    watch,
)

CDN = "https://scontent.cdninstagram.com/v/{}.mp4"


@pytest.fixture(autouse=True)
def _no_retry_pause(monkeypatch):
    monkeypatch.setenv("OFM_IG_RETRY_JITTER_S", "0")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(task_helper_uploads, "ASSETS_ROOT", tmp_path / "assets")
    monkeypatch.setattr(reels, "UPLOADS_ROOT", tmp_path / "uploads")
    c = TestClient(app)
    c.post("/login", data={"password": "test-va", "next": "/"})
    return c


def _found(
    username: str, views: int = 60_000, code: str = "Dekxh8BJYdu"
) -> ProfileReels:
    profile = Profile(user_id="1", username=username, followers=1000, is_private=False)
    reel = Reel(
        code=code,
        taken_at=0,
        duration_s=13.0,
        views=views,
        followers=1000,
        thumbnail_url="https://scontent.cdninstagram.com/t.jpg",
        video_url=CDN.format("a"),
    )
    return ProfileReels(profile=profile, scanned=24, reels=[reel])


@pytest.fixture
def scans(monkeypatch):
    """Every scan finds `_found(username)`; the usernames scanned, in order.
    The test suite runs enqueued jobs inline, so a scan queued by POST
    /reels/watch has run by the time the response is back."""
    seen: list[str] = []

    def fake_scan(_client, username, min_views, max_duration_s, max_age_s, limit):
        seen.append(username)
        assert (min_views, max_duration_s, max_age_s, limit) == (
            50_000,
            15.0,
            14 * 86400,
            120,
        )
        return _found(username, code=f"r_{username}")

    monkeypatch.setattr(reel_watch, "scan_profile", fake_scan)
    return seen


def test_page_is_in_the_nav(client):
    html = client.get("/reels").text
    assert 'href="/reels"' in html
    assert 'id="reels-form"' in html
    assert "No reels yet" in html


def test_watch_form_takes_the_handle_off_each_line():
    form = WatchForm(
        profiles="https://www.instagram.com/Sheep_Love33/\n@safaree — Safaree Samuels\n\n"
        "https://instagram.com/kikiumi2026?cplk=x\nsheep_love33",
        group="  Gay  ",
    )
    assert form.usernames == ["sheep_love33", "safaree", "kikiumi2026"]
    assert form.group == "gay"
    with pytest.raises(ValueError, match="bad-name!"):
        _ = WatchForm(profiles="ok\nbad-name! extra").usernames


def test_adding_scans_the_new_profiles_in_the_background(client, scans):
    r = client.post(
        "/reels/watch",
        data={
            "profiles": "https://www.instagram.com/Sheep_Love33/\n@brookslockvip",
            "group": "Asian",
        },
    )
    assert r.json() == {
        "added": ["sheep_love33", "brookslockvip"],
        "already_watched": 0,
    }
    assert sorted(scans) == ["brookslockvip", "sheep_love33"]
    assert [(p.username, p.group, p.pending) for p in list_watched()] == [
        ("sheep_love33", "asian", False),
        ("brookslockvip", "asian", False),
    ]

    html = client.get("/reels").text
    assert 'data-code="r_sheep_love33"' in html
    # Only a lazy thumbnail is in the page; the video is created on hover.
    assert 'loading="lazy"' in html
    assert "<video" not in html
    assert f'data-video-url="{CDN.format("a")}"' in html


def test_re_adding_moves_a_profile_to_the_group_without_scanning_it(client, scans):
    client.post("/reels/watch", data={"profiles": "old"})
    r = client.post("/reels/watch", data={"profiles": "old\nnew", "group": "gay"})
    assert r.json() == {"added": ["new"], "already_watched": 1}
    assert scans == ["old", "new"]
    assert [(p.username, p.group) for p in list_watched()] == [
        ("old", "gay"),
        ("new", "gay"),
    ]
    assert list_groups() == ["gay"]


@pytest.mark.usefixtures("scans")
def test_feed_filters_by_group(client):
    client.post("/reels/watch", data={"profiles": "a1", "group": "asian"})
    client.post("/reels/watch", data={"profiles": "g1", "group": "gay"})

    html = client.get("/reels", params={"group": "gay"}).text
    assert 'data-code="r_g1"' in html
    assert 'data-code="r_a1"' not in html
    assert 'href="/reels?group=asian"' in html
    assert 'data-gallery-endpoint="/reels/feed?group=gay"' in html


def test_feed_pages(client, monkeypatch):
    monkeypatch.setenv("OFM_IG_FEED_PAGE_SIZE", "2")
    profiles = [
        WatchedProfile(
            username=f"u{i}", added_at=0, result=_found(f"u{i}", 100_000 - i, f"c{i}")
        )
        for i in range(5)
    ]
    monkeypatch.setattr(reels, "list_watched", lambda _group=None: profiles)

    first = client.get("/reels").text
    assert 'data-code="c0"' in first
    assert 'data-code="c2"' not in first
    assert 'data-next-offset="2"' in first
    last = client.get("/reels/feed", params={"offset": 4}).text
    assert 'data-code="c4"' in last
    assert "gallery-sentinel" not in last


def test_watchlist_marks_pending_profiles(client):
    watch("waiting")
    rows = client.get("/reels/watchlist").text
    row = rows.split('data-username="waiting"', 1)[1].split(">", 1)[0]
    assert "data-pending" in row
    assert "scanning" in rows


def test_login_only_reel_is_shown_but_not_pickable(client, monkeypatch):
    def gated(_client, username, *_args):
        found = _found(username)
        found.reels[0] = found.reels[0].model_copy(
            update={"video_url": None, "duration_s": None}
        )
        return found

    monkeypatch.setattr(reel_watch, "scan_profile", gated)
    client.post("/reels/watch", data={"profiles": "x"})
    html = client.get("/reels").text
    assert 'aria-disabled="true"' in html
    assert "login only" in html
    assert "data-video-url" not in html


def test_uploading_cookies_unlocks_stored_login_only_reels(
    client, tmp_path, monkeypatch
):
    def gated(_client, username, *_args):
        found = _found(username)
        found.reels[0] = found.reels[0].model_copy(
            update={"video_url": None, "duration_s": None}
        )
        return found

    monkeypatch.setattr(reel_watch, "scan_profile", gated)
    client.post("/reels/watch", data={"profiles": "x"})
    scanned_at = get_watched("x").scanned_at
    asked = []

    def logged_in(_self, pk):
        asked.append(pk)
        return ReelMedia(video_url=CDN.format("u"), duration_s=9.0, taken_at=5)

    monkeypatch.setattr(reel_watch.InstagramClient, "media_logged_in", logged_in)
    monkeypatch.setenv("OFM_COOKIES_FILE", str(tmp_path / "cookies.txt"))
    client.post("/login", data={"password": "test-admin", "next": "/"})
    client.post("/cookies", files={"file": ("cookies.txt", b"# Netscape")})

    watched = get_watched("x")
    assert asked == [_found("x").reels[0].pk]
    assert watched.result.reels[0].video_url == CDN.format("u")
    assert watched.scanned_at == scanned_at
    assert "login only" not in client.get("/reels").text


def test_feed_is_every_profiles_reels_most_viewed_first():
    def watched(username, views):
        return WatchedProfile(
            username=username, added_at=0, result=_found(username, views)
        )

    feed = FeedItem.of([watched("a", 60_000), watched("b", 2_000_000), watched("c", 0)])
    assert [(i.username, i.reel.tier) for i in feed] == [
        ("b", "legendary"),
        ("a", "common"),
        ("c", None),
    ]


def test_group_without_profiles_is_a_clear_400(client):
    r = client.post("/reels/watch", data={"group": "asian"})
    assert r.status_code == 400
    assert r.json()["detail"] == "Add at least one profile"


def test_bad_line_adds_nothing(client):
    r = client.post("/reels/watch", data={"profiles": "ok_name\nbad-name!"})
    assert r.status_code == 400
    assert list_watched() == []


def test_failed_rescan_keeps_the_last_result_next_to_its_error(
    client, scans, monkeypatch
):
    client.post("/reels/watch", data={"profiles": "x"})

    def fail(*_args, **_kwargs):
        msg = "profile not found"
        raise InstagramError(msg)

    monkeypatch.setattr(reel_watch, "scan_profile", fail)
    reel_watch.scan_watched("x")
    html = client.get("/reels").text
    assert "profile not found" in html
    assert 'data-code="r_x"' in html
    assert scans == ["x"]


def test_rate_limit_stops_the_batch_and_leaves_the_rest_due(monkeypatch):
    seen = []

    def limited(_client, username, *_args):
        seen.append(username)
        if username == "b":
            msg = "rate-limited"
            raise InstagramRateLimitedError(msg)
        return _found(username)

    monkeypatch.setattr(reel_watch, "scan_profile", limited)
    for u in ("a", "b", "c"):
        watch(u)
    reel_watch.scan_all(["a", "b", "c"])
    assert seen == ["a", "b"]
    assert [(p.username, p.pending, p.error) for p in list_watched()] == [
        ("a", False, None),
        ("b", True, None),
        ("c", True, None),
    ]


def test_not_found_is_retried_on_another_proxy_and_never_drops_the_profile(
    client, scans, monkeypatch
):
    client.post("/reels/watch", data={"profiles": "real"})
    monkeypatch.setenv("OFM_IG_PROXIES", "a:1,b:2,c:3")
    used = []

    def flagged_ip(_client, username, *_args):
        used.append(username)
        msg = "profile not found"
        raise ProfileNotFoundError(msg)

    monkeypatch.setattr(reel_watch, "scan_profile", flagged_ip)
    reel_watch.scan_all(["real"])
    watched = get_watched("real")
    assert used == ["real", "real", "real"]
    assert watched is not None
    assert "no such profile (3 tries)" in watched.error
    assert watched.result is not None  # the last good scan stays on show


def _not_found(_client, username, *_args):
    msg = "profile not found"
    raise ProfileNotFoundError(msg)


def test_a_new_profile_confirmed_gone_is_not_added(monkeypatch):
    monkeypatch.setenv("OFM_IG_PROXIES", "a:1,b:2")
    monkeypatch.setattr(reel_watch, "scan_profile", _not_found)
    monkeypatch.setattr(
        reel_watch.InstagramClient, "exists_logged_in", lambda _self, _u: False
    )
    watch("deleted_account")
    reel_watch.scan_all(["deleted_account"])
    assert get_watched("deleted_account") is None


def test_a_new_profile_the_login_still_sees_is_kept(monkeypatch):
    monkeypatch.setenv("OFM_IG_PROXIES", "a:1,b:2")
    monkeypatch.setattr(reel_watch, "scan_profile", _not_found)
    monkeypatch.setattr(
        reel_watch.InstagramClient, "exists_logged_in", lambda _self, _u: True
    )
    watch("flagged_proxies")
    reel_watch.scan_all(["flagged_proxies"])
    assert "no such profile" in get_watched("flagged_proxies").error


def test_a_private_profile_is_not_added_but_a_known_one_turning_private_stays(
    client, scans, monkeypatch
):
    client.post("/reels/watch", data={"profiles": "known"})  # scanned fine

    def private(_client, username, *_args):
        msg = "profile is private"
        raise ProfilePrivateError(msg)

    monkeypatch.setattr(reel_watch, "scan_profile", private)
    watch("new_private")
    reel_watch.scan_all(["new_private", "known"])
    assert get_watched("new_private") is None
    known = get_watched("known")
    assert known.error == "profile is private"
    assert known.result is not None  # its good reels stay


def test_instagram_errors_on_every_try_are_shown_and_the_batch_goes_on(
    monkeypatch,
):
    monkeypatch.setenv("OFM_IG_PROXIES", "a:1,b:2")
    seen = []

    def flaky(_client, username, *_args):
        seen.append(username)
        if username == "down":
            msg = "Instagram answered 503"
            raise InstagramUnavailableError(msg)
        return _found(username)

    monkeypatch.setattr(reel_watch, "scan_profile", flaky)
    watch("down")
    watch("up")
    reel_watch.scan_all(["down", "up"])
    assert seen == ["down", "down", "up"]
    assert "errors (2 tries)" in get_watched("down").error
    assert get_watched("up").error is None


def test_tries_that_never_reach_instagram_record_nothing(monkeypatch):
    monkeypatch.setenv("OFM_IG_PROXIES", "a:1,b:2,c:3")

    def dropped(_client, username, *_args):
        if username == "x":
            raise requests.exceptions.ReadTimeout
        return _found(username)

    monkeypatch.setattr(reel_watch, "scan_profile", dropped)
    watch("x")
    watch("y")
    reel_watch.scan_all(["x", "y"])
    x = get_watched("x")
    assert (x.pending, x.error) == (True, None)  # still due, nothing blamed
    assert get_watched("y").error is None


def test_a_network_outage_stops_the_batch_without_blaming_anyone(monkeypatch):
    monkeypatch.setenv("OFM_IG_PROXIES", "a:1")
    used = []

    def offline(username, proxy=None):
        used.append((username, proxy))
        raise requests.exceptions.ProxyError if proxy else requests.ConnectionError

    monkeypatch.setattr(reel_watch, "scan_watched", offline)
    for u in ("x", "y", "z"):
        watch(u)
    reel_watch.scan_all(["x", "y", "z"])
    # x kills the only proxy, y goes direct and fails: the batch stops there.
    assert used == [("x", "http://a:1"), ("y", None)]
    assert [(p.pending, p.error) for p in list_watched()] == [(True, None)] * 3


def test_a_dead_proxy_leaves_the_pool_for_the_rest_of_the_job(monkeypatch):
    monkeypatch.setenv("OFM_IG_PROXIES", "dead:1,b:2,c:3")
    used = []

    def scan(username, proxy=None):
        used.append(proxy)
        if proxy == "http://dead:1":
            raise requests.exceptions.ProxyError

    monkeypatch.setattr(reel_watch, "scan_watched", scan)
    reel_watch.scan_all(["x", "y", "z", "w"])
    assert used == [
        "http://dead:1",
        "http://b:2",
        "http://c:3",
        "http://b:2",
        "http://c:3",
    ]


def test_one_rate_limited_try_does_not_stop_the_batch(monkeypatch):
    monkeypatch.setenv("OFM_IG_PROXIES", "a:1,b:2")
    used = []

    def scan(username, proxy=None):
        used.append(username)
        if username == "x":
            if proxy == "http://a:1":
                msg = "gone"
                raise ProfileNotFoundError(msg)
            msg = "429"
            raise InstagramRateLimitedError(msg)

    monkeypatch.setattr(reel_watch, "scan_watched", scan)
    watch("x")
    reel_watch.scan_all(["x", "y"])
    assert used == ["x", "x", "y"]
    assert "no such profile (2 tries)" in get_watched("x").error


def test_with_every_proxy_dead_the_job_goes_direct():
    pool = ProxyPool(["http://a:1"])
    pool.drop("http://a:1")
    assert pool.next() is None


def test_row_dropdown_moves_a_profile(client):
    watch("x", "asian")
    r = client.post("/reels/group/x", data={"group": " Gay "})
    assert r.json() == {"username": "x", "group": "gay"}
    assert get_watched("x").group == "gay"
    client.post("/reels/group/x", data={"group": ""})
    assert get_watched("x").group is None
    assert client.post("/reels/group/nobody", data={"group": "gay"}).status_code == 404


def test_a_new_group_is_offered_before_any_profile_is_in_it(client):
    watch("x")
    r = client.post("/reels/groups", data={"name": "  Gym  Bros "})
    assert r.json() == {"group": "gym bros"}
    assert list_groups() == ["gym bros"]
    rows = client.get("/reels/watchlist").text
    assert '<option value="gym bros" >' in rows
    assert client.post("/reels/groups", data={"name": " "}).status_code == 400


def test_feed_filters_by_rank_and_group_together(client, monkeypatch):
    profiles = [
        WatchedProfile(
            username="a", group="gay", added_at=0, result=_found("a", 60_000, "small")
        ),
        WatchedProfile(
            username="b", group="gay", added_at=0, result=_found("b", 1_200_000, "huge")
        ),
        WatchedProfile(
            username="c", added_at=0, result=_found("c", 2_000_000, "other")
        ),
    ]
    monkeypatch.setattr(
        reels,
        "list_watched",
        lambda group=None: [p for p in profiles if group is None or p.group == group],
    )
    html = client.get("/reels", params={"group": "gay", "rank": "legendary"}).text
    assert 'data-code="huge"' in html
    assert 'data-code="small"' not in html
    assert 'data-code="other"' not in html
    # Each chip row keeps the other's choice.
    assert 'href="/reels?group=gay&amp;rank=epic"' in html
    assert 'href="/reels?rank=legendary"' in html
    assert 'data-gallery-endpoint="/reels/feed?group=gay&amp;rank=legendary"' in html


def test_a_rank_chip_shows_exactly_that_rank_on_every_page(client, monkeypatch):
    """Uncommon = 100k-200k only; scrolling keeps group and rank."""
    monkeypatch.setenv("OFM_IG_FEED_PAGE_SIZE", "2")
    views = [150_000, 140_000, 130_000, 120_000, 110_000, 2_000_000, 300_000, 60_000]
    profiles = [
        WatchedProfile(
            username=f"u{i}",
            group="gay",
            added_at=0,
            result=_found(f"u{i}", v, f"c{v}"),
        )
        for i, v in enumerate(views)
    ]
    monkeypatch.setattr(
        reels,
        "list_watched",
        lambda group=None: [p for p in profiles if group is None or p.group == group],
    )
    first = client.get("/reels", params={"group": "gay", "rank": "uncommon"}).text
    assert [
        c for c in ("c150000", "c140000", "c130000") if f'data-code="{c}"' in first
    ] == ["c150000", "c140000"]
    for other in ("c2000000", "c300000", "c60000"):
        assert f'data-code="{other}"' not in first
    assert 'data-next-offset="2"' in first

    # What gallery-scroll.js asks for on scroll: the same filters, next offset.
    page2 = client.get(
        "/reels/feed", params={"group": "gay", "rank": "uncommon", "offset": 2}
    ).text
    assert 'data-code="c130000"' in page2
    assert 'data-code="c120000"' in page2
    last = client.get(
        "/reels/feed", params={"group": "gay", "rank": "uncommon", "offset": 4}
    ).text
    assert 'data-code="c110000"' in last
    assert "gallery-sentinel" not in last  # the scroll ends
    for page in (page2, last):
        for other in ("c2000000", "c300000", "c60000"):
            assert f'data-code="{other}"' not in page


def test_feed_filters_by_follower_multiple(client, monkeypatch):
    def found(username, followers, views):
        result = _found(username, views=views)
        reel = result.reels[0].model_copy(update={"followers": followers})
        profile = result.profile.model_copy(update={"followers": followers})
        result = result.model_copy(update={"reels": [reel], "profile": profile})
        return WatchedProfile(
            username=username, added_at=0, scanned_at=1, result=result
        )

    profiles = [
        found("small_viral", 10_000, 120_000),  # 12x its followers
        found("mid", 50_000, 160_000),  # 3.2x
        found("big", 1_000_000, 900_000),  # 0.9x
    ]
    monkeypatch.setattr(reels, "list_watched", lambda _group=None: profiles)

    def shown(query):
        return [
            u
            for u in ("small_viral", "mid", "big")
            if f'data-username="{u}"' in client.get(f"/reels/feed{query}").text
        ]

    assert shown("") == ["small_viral", "mid", "big"]
    assert shown("?ratio=3") == ["small_viral", "mid"]
    assert shown("?ratio=10") == ["small_viral"]
    html = client.get("/reels?ratio=5&rank=epic").text
    nav = html.split('aria-labelledby="reels-filter-reach"', 1)[1].split("</nav>", 1)[0]
    for n in (2, 3, 5, 10):
        assert f"\N{MULTIPLICATION SIGN}{n}+" in nav
    assert 'href="/reels?rank=epic&amp;ratio=5" aria-current="page"' in nav


def test_an_empty_filter_says_so(client):
    html = client.get("/reels", params={"group": "gay", "rank": "legendary"}).text
    assert "No legendary reels in gay right now." in html


def test_rank_chips_are_best_first_with_their_ranges(client):
    html = client.get("/reels").text
    labels = [
        "legendary 1M+",
        "epic 1M\N{EN DASH}500k",
        "rare 500k\N{EN DASH}200k",
        "uncommon 200k\N{EN DASH}100k",
        "common 100k\N{EN DASH}50k",
    ]
    positions = [html.index(label) for label in labels]
    assert positions == sorted(positions)


def _with_recent(found: ProfileReels, views: list[int]) -> ProfileReels:
    recent = [ReelStat(code=f"s{i}", views=v, posted_at=0) for i, v in enumerate(views)]
    return found.model_copy(update={"recent": recent, "window_days": 14})


def test_scan_stats_cover_every_reel_of_the_window():
    found = _with_recent(_found("x"), [10, 1_000, 60_000, 3_000_000])
    dumped = found.model_dump(mode="json")
    assert (dumped["views_total"], dumped["views_max"], dumped["views_median"]) == (
        3_061_010,
        3_000_000,
        30_500,
    )
    assert dumped["eligible"] == 1


def test_every_scan_appends_its_numbers_to_the_history(monkeypatch):
    from sqlalchemy import select

    from ofmhelpers.web.db.models import ProfileScanStatRow
    from ofmhelpers.web.db.session import session_scope

    monkeypatch.setattr(
        reel_watch,
        "scan_profile",
        lambda _c, username, *_a: _with_recent(_found(username), [100, 200_000]),
    )
    watch("x", "gay")
    reel_watch.scan_watched("x")
    reel_watch.scan_watched("x")
    with session_scope() as s:
        rows = s.execute(select(ProfileScanStatRow)).scalars().all()
        assert [
            (r.username, r.group_name, r.views_total, r.reels, r.eligible, r.followers)
            for r in rows
        ] == [
            ("x", "gay", 200_100, 2, 1, 1000),
            ("x", "gay", 200_100, 2, 1, 1000),
        ]


def test_watch_list_shows_two_week_views_and_crowns_the_most_viewed(
    client, monkeypatch
):
    def scanned(username, views):
        return WatchedProfile(
            username=username,
            added_at=0,
            scanned_at=1,
            result=_with_recent(_found(username), views),
        )

    profiles = [scanned("small", [5_000]), scanned("king", [2_000_000, 500_000])]
    monkeypatch.setattr(reels, "list_watched", lambda _group=None: profiles)
    rows = client.get("/reels/watchlist").text
    king = rows.split('data-username="king"', 1)[1].split("</li>", 1)[0]
    small = rows.split('data-username="small"', 1)[1].split("</li>", 1)[0]
    assert 'data-tier="legendary"' in king
    assert "#1 views" in king
    assert "2.5M views" in king
    assert 'data-tier="legendary"' not in small
    assert "5k views" in small


def test_leaderboard_leads_with_most_views_then_most_reels(client, monkeypatch):
    def scanned(username, reel_count, window_views):
        found = _with_recent(_found(username), [window_views])
        return WatchedProfile(
            username=username,
            added_at=0,
            scanned_at=1,
            group="asian",
            result=found.model_copy(update={"reels": found.reels * reel_count}),
        )

    profiles = [
        scanned("few", 3, 1_000_000),
        scanned("sheep_love33", 15, 13_700_000),
        scanned("nnnaomi.chuu", 10, 14_100_000),
        WatchedProfile(username="new", added_at=0),
    ]
    board = Leaderboard.of(profiles)
    assert [p.username for p in board.rows] == [
        "nnnaomi.chuu",
        "sheep_love33",
        "new",
        "few",
    ]
    assert (board.top_views, board.top_reels) == ("nnnaomi.chuu", "sheep_love33")

    monkeypatch.setattr(reels, "list_watched", lambda _group=None: profiles)
    rows = client.get("/reels/watchlist?group=asian").text
    sheep = rows.split('data-username="sheep_love33"', 1)[1].split("</li>", 1)[0]
    assert 'data-tier="epic"' in sheep
    assert "#2 viral reels" in sheep
    assert rows.index("nnnaomi.chuu") < rows.index("sheep_love33")


def test_leaderboard_without_scans_outlines_nobody():
    board = Leaderboard.of([WatchedProfile(username="new", added_at=0)])
    assert (board.top_views, board.top_reels) == (None, None)


def test_watch_list_is_most_feed_reels_first():
    def watched(username, reel_views, followers=1000):
        found = _found(username)
        reels = [
            r
            for v in reel_views
            for r in [found.reels[0].model_copy(update={"views": v})]
        ]
        profile = found.profile.model_copy(update={"followers": followers})
        return WatchedProfile(
            username=username,
            added_at=0,
            scanned_at=1,
            result=found.model_copy(update={"reels": reels, "profile": profile}),
        )

    profiles = [
        watched("huge_account_one_reel", [60_000], followers=5_000_000),
        WatchedProfile(username="new", added_at=0),
        watched("three_reels", [60_000, 60_000, 60_000], followers=5_000),
        watched("none", []),
        watched("two_big", [900_000, 900_000]),
        watched("two_small", [60_000, 60_000]),
    ]
    assert [p.username for p in WatchedProfile.by_reels(profiles)] == [
        "new",
        "three_reels",
        "two_big",
        "two_small",
        "huge_account_one_reel",
        "none",
    ]


def test_watch_list_rows_offer_every_group(client):
    watch("a", "asian")
    watch("g", "gay")
    rows = client.get("/reels/watchlist").text
    row_a = rows.split('data-username="a"', 1)[1].split("</li>", 1)[0]
    assert '<option value="asian" selected>' in row_a
    assert '<option value="gay" >' in row_a


def test_proxy_list_is_read_from_the_env(monkeypatch):
    monkeypatch.setenv("OFM_IG_PROXIES", " u:p@h:1 , http://u:p@h:2,,")
    assert reels.settings.instagram.proxy_urls == ["http://u:p@h:1", "http://u:p@h:2"]


def test_a_rate_limited_profile_moves_to_the_next_proxy(monkeypatch):
    monkeypatch.setenv("OFM_IG_PROXIES", "a:1,b:2,c:3")
    used = []

    def scan(username, proxy=None):
        used.append((username, proxy))
        if proxy == "http://a:1":
            msg = "429"
            raise InstagramRateLimitedError(msg)

    monkeypatch.setattr(reel_watch, "scan_watched", scan)
    reel_watch.scan_all(["x", "y"])
    assert used == [("x", "http://a:1"), ("x", "http://b:2"), ("y", "http://c:3")]


def test_a_proxy_that_breaks_tls_moves_to_the_next_proxy(monkeypatch):
    monkeypatch.setenv("OFM_IG_PROXIES", "a:1,b:2")
    used = []

    def scan(_client, username, *_args):
        used.append(username)
        if len(used) == 1:
            msg = "handshake failure"
            raise requests.exceptions.SSLError(msg)
        return _found(username)

    monkeypatch.setattr(reel_watch, "scan_profile", scan)
    watch("x")
    reel_watch.scan_all(["x"])
    assert used == ["x", "x"]
    assert [(p.username, p.error) for p in list_watched()] == [("x", None)]


def test_a_batch_stops_when_every_retry_is_rate_limited(monkeypatch):
    monkeypatch.setenv("OFM_IG_PROXIES", "a:1,b:2")
    used = []

    def scan(username, proxy=None):
        used.append(username)
        msg = "429"
        raise InstagramRateLimitedError(msg)

    monkeypatch.setattr(reel_watch, "scan_watched", scan)
    reel_watch.scan_all(["x", "y"])
    assert used == ["x", "x"]  # both proxies tried for x, y left for later


def test_unwatch_needs_a_watched_profile(client):
    watch("x")
    assert client.post("/reels/unwatch/x").status_code == 200
    assert get_watched("x") is None
    assert client.post("/reels/unwatch/x").status_code == 404


def test_sweeper_rescans_only_stale_profiles_once(scans):
    watch("fresh")
    watch("stale")
    reel_watch.scan_watched("fresh")
    assert scans == ["fresh"]

    # "fresh" was scanned just now; "stale" never was.
    assert reel_watch.queue_due_scans() == ["stale"]
    assert scans == ["fresh", "stale"]
    stale = get_watched("stale")
    assert stale is not None
    assert stale.result is not None
    # Nothing is due again until the interval has passed.
    assert reel_watch.queue_due_scans() == []


def test_a_claimed_profile_is_not_claimed_twice():
    watch("x")
    assert claim_due(0, 3600) == ["x"]
    assert claim_due(0, 3600) == []
    # ...until the claim expires (a scan killed mid-way).
    assert claim_due(0, 0) == ["x"]


def test_clone_downloads_picked_reels_and_opens_clone_with_them(client, monkeypatch):
    def fake_download(_self, url, dest: Path):
        dest.write_bytes(url.encode())
        return dest

    monkeypatch.setattr(reels.InstagramClient, "download_video", fake_download)
    picked = [
        {"code": "A1", "video_url": CDN.format("a")},
        {"code": "B2", "video_url": CDN.format("b")},
    ]
    r = client.post("/reels/clone", data={"reels": json.dumps(picked)})
    assert r.status_code == 200
    redirect = r.json()["redirect"]
    videos = parse_qs(urlsplit(redirect).query)["video"]
    assert [Path(v).name.split("__", 1)[1] for v in videos] == ["A1.mp4", "B2.mp4"]

    # /clone opens with exactly those videos picked.
    html = client.get(redirect).text
    form_tag = html.split('id="clone-form"', 1)[1].split(">", 1)[0]
    prefill = json.loads(form_tag.split("data-prefill-videos='", 1)[1].split("'", 1)[0])
    assert prefill == videos


def test_download_answers_one_mp4_or_a_zip_of_several(client, tmp_path, monkeypatch):
    def fake_download(_self, url, dest: Path):
        dest.write_bytes(url.encode())
        return dest

    monkeypatch.setattr(reels.InstagramClient, "download_video", fake_download)
    one = [{"code": "A1", "video_url": CDN.format("a")}]
    r = client.post("/reels/download", data={"reels": json.dumps(one)})
    assert r.headers["content-type"] == "video/mp4"
    assert 'filename="A1.mp4"' in r.headers["content-disposition"]
    assert r.content == CDN.format("a").encode()

    two = [*one, {"code": "B2", "video_url": CDN.format("b")}]
    r = client.post("/reels/download", data={"reels": json.dumps(two)})
    assert r.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        assert sorted(zf.namelist()) == ["A1.mp4", "B2.mp4"]
    # Nothing stays on the server once the file is sent.
    assert not any((tmp_path / "uploads" / "reels").iterdir())


def test_download_refuses_an_empty_pick(client):
    r = client.post("/reels/download", data={"reels": "[]"})
    assert r.status_code == 400


def test_clone_refuses_non_cdn_urls_and_empty_picks(client):
    bad = [{"code": "A1", "video_url": "https://evil.com/a.mp4"}]
    assert (
        client.post("/reels/clone", data={"reels": json.dumps(bad)}).status_code == 400
    )
    assert client.post("/reels/clone", data={"reels": "[]"}).status_code == 400


def test_clone_page_drops_prefill_paths_outside_the_store(client, tmp_path):
    outside = tmp_path / "x.mp4"
    outside.write_bytes(b"v")
    html = client.get("/clone", params={"video": [str(outside), "/nope.mp4"]}).text
    assert "data-prefill-videos='[]'" in html


def test_feed_hides_stored_reels_past_the_length_cap(client, monkeypatch):
    """A reel stored by an older scan (30 s cap) disappears as soon as the
    cap drops, not at the profile's next rescan."""

    def stored(username, length):
        result = _found(username, code=f"c_{username}")
        reel = result.reels[0].model_copy(update={"duration_s": length})
        return WatchedProfile(
            username=username,
            added_at=0,
            scanned_at=1,
            result=result.model_copy(update={"reels": [reel]}),
        )

    profiles = [stored("short", 15.0), stored("long", 15.7), stored("gated", None)]
    monkeypatch.setattr(reels, "list_watched", lambda _group=None: profiles)

    html = client.get("/reels/feed").text

    assert 'data-code="c_short"' in html
    assert 'data-code="c_gated"' in html
    assert 'data-code="c_long"' not in html
