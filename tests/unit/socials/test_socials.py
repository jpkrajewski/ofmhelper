"""socials/: the platform registry, the Instagram platform's mapping of its
client (and its errors) onto the generic shapes, and the scan numbers."""

import pytest
import requests

from ofmhelpers.instagram import (
    InstagramError,
    InstagramRateLimitedError,
    InstagramUnavailableError,
    ProfileNotFoundError,
)
from ofmhelpers.instagram.models import Profile, ReelSummary
from ofmhelpers.socials import (
    AccountProfile,
    AccountStats,
    PostStat,
    PostViews,
    RateLimitedError,
    RetryableError,
    SocialError,
    ViewWindow,
    get_platform,
    platforms,
)
from ofmhelpers.socials.platforms import instagram as ig_platform

NOW = 1_800_000_000.0
DAY = 86400


def _pk(posted_at: float) -> str:
    """A media id whose embedded time is `posted_at`."""
    return str(int((posted_at - 1314220021.721) * 1000) << 23)


class _FakeClient:
    """`logged_in`: what the uploaded login lists (None: no working login;
    an exception: it fails)."""

    def __init__(
        self, proxy=None, *, private=False, reels=(), error=None, logged_in=None
    ):
        self.proxy = proxy
        self.private = private
        self._reels = reels
        self.error = error
        self.logged_in = logged_in
        self.reels_read = False

    def open_profile(self, username):
        if self.error:
            raise self.error
        return Profile(
            user_id="1",
            username=username,
            followers=1234,
            is_private=self.private,
            full_name="Yuki",
            following=50,
            is_verified=True,
            avatar_url="https://scontent-iad3-2.cdninstagram.com/a.jpg",
        )

    def reels(self, _user_id):
        self.reels_read = True
        yield from self._reels

    def reels_logged_in(self, _username, _user_id, limit):
        if isinstance(self.logged_in, BaseException):
            raise self.logged_in
        return None if self.logged_in is None else list(self.logged_in)[:limit]


@pytest.fixture
def fake_client(monkeypatch):
    def install(**kwargs):
        made = []

        def build(proxy=None, cookies=None):
            client = _FakeClient(proxy, **kwargs)
            client.cookies = cookies
            made.append(client)
            return client

        monkeypatch.setattr(ig_platform, "InstagramClient", build)
        return made

    return install


def test_registry_has_instagram():
    assert "instagram" in platforms()
    assert get_platform("instagram").label == "Instagram"
    with pytest.raises(KeyError):
        get_platform("myspace")


@pytest.mark.parametrize(
    "raw",
    ["Yuki_Kanasiva", "@yuki_kanasiva", "https://www.instagram.com/yuki_kanasiva/"],
)
def test_instagram_handle(raw):
    ig = get_platform("instagram")
    assert ig.handle(raw) == "yuki_kanasiva"
    assert ig.profile_url("yuki_kanasiva") == "https://www.instagram.com/yuki_kanasiva/"


def test_instagram_handle_rejects_garbage():
    with pytest.raises(ValueError, match="Instagram username"):
        get_platform("instagram").handle("not a handle!")


def test_instagram_fetch_reads_profile_and_reels(fake_client, monkeypatch):
    monkeypatch.setenv("OFM_IG_TRACK_MAX_REELS", "2")
    reels = [
        ReelSummary(pk=_pk(NOW - DAY), code="A", play_count=100),
        ReelSummary(pk=_pk(NOW - 10 * DAY), code="B", play_count=None),
        ReelSummary(pk=_pk(NOW - 50 * DAY), code="C", play_count=999),
    ]
    made = fake_client(reels=reels)
    stats = get_platform("instagram").fetch("yuki", "http://p:1")
    assert made[0].proxy == "http://p:1"
    assert stats.profile.handle == "yuki"
    assert stats.profile.display_name == "Yuki"
    assert stats.profile.following == 50
    assert stats.profile.is_verified
    assert stats.profile.avatar_url.startswith("https://scontent.cdninstagram.com/")
    # Capped at track_max_reels (so not complete); missing play counts are 0.
    assert [(p.id, p.views) for p in stats.posts] == [("A", 100), ("B", 0)]
    assert not stats.complete
    assert stats.posts[0].url == "https://www.instagram.com/reel/A/"
    assert stats.posts[0].posted_at == pytest.approx(NOW - DAY, abs=1)


def test_working_login_lists_every_reel(fake_client):
    """Instagram hides sensitive reels from logged-out visitors (danywalking:
    8 of 15 visible logged out, 2026-10-20); the login sees them all."""
    hidden = [ReelSummary(pk=_pk(NOW - i * 3600), code=f"R{i}") for i in range(15)]
    made = fake_client(reels=hidden[:8], logged_in=hidden)
    stats = get_platform("instagram").fetch("yuki", None)
    assert len(stats.posts) == 15
    assert not stats.limited
    assert not made[0].reels_read


def test_the_scan_logs_in_with_the_cookies_it_is_given(fake_client):
    """The owner's cookies.txt reaches the client; none means logged out."""
    made = fake_client()
    get_platform("instagram").fetch("yuki", None, "owner-jar")
    get_platform("instagram").fetch("yuki", None)
    assert [c.cookies for c in made] == ["owner-jar", None]


@pytest.mark.parametrize("login", [None, requests.ConnectionError("down")])
def test_without_a_working_login_the_logged_out_view_is_marked_limited(
    fake_client, login
):
    fake_client(reels=[ReelSummary(pk=_pk(NOW), code="A")], logged_in=login)
    stats = get_platform("instagram").fetch("yuki", None)
    assert [p.id for p in stats.posts] == ["A"]
    assert stats.limited
    assert stats.metrics().limited


def test_logged_in_listing_can_be_switched_off(fake_client, monkeypatch):
    monkeypatch.setenv("OFM_IG_TRACK_LOGGED_IN", "false")
    fake_client(reels=[], logged_in=[ReelSummary(pk=_pk(NOW), code="A")])
    stats = get_platform("instagram").fetch("yuki", None)
    assert stats.posts == []
    assert stats.limited


def test_instagram_private_account_keeps_its_numbers_without_reels(fake_client):
    made = fake_client(private=True, reels=[ReelSummary(pk=_pk(NOW), code="A")])
    stats = get_platform("instagram").fetch("yuki", None)
    assert stats.profile.is_private
    assert stats.profile.followers == 1234
    assert stats.posts == []
    assert not stats.complete  # nothing was read
    assert not made[0].reels_read


@pytest.mark.parametrize(
    ("error", "expected", "reached", "dead"),
    [
        (InstagramRateLimitedError("429"), RateLimitedError, True, False),
        (InstagramUnavailableError("502"), RetryableError, True, False),
        (ProfileNotFoundError("gone"), RetryableError, True, False),
        (requests.ConnectionError("down"), RetryableError, False, False),
        (requests.exceptions.ProxyError("bad proxy"), RetryableError, False, True),
        (requests.exceptions.ConnectTimeout("slow"), RetryableError, False, True),
    ],
)
def test_instagram_errors_map_to_retryable(fake_client, error, expected, reached, dead):
    fake_client(error=error)
    with pytest.raises(expected) as caught:
        get_platform("instagram").fetch("yuki", None)
    assert caught.value.reached is reached
    assert caught.value.dead_proxy is dead


def test_instagram_lasting_error_is_a_social_error(fake_client):
    fake_client(error=InstagramError("Instagram changed its query"))
    with pytest.raises(SocialError) as caught:
        get_platform("instagram").fetch("yuki", None)
    assert not isinstance(caught.value, RetryableError)


def test_metrics_are_the_scan_totals():
    stats = AccountStats(
        profile=AccountProfile(handle="y", followers=10, following=3),
        posts=[
            PostStat(id="a", url="u", views=100, posted_at=NOW - 1 * DAY),
            PostStat(id="b", url="u", views=47, posted_at=NOW - 100 * DAY),
        ],
        complete=False,
        limited=True,
        scanned_at=NOW,
    )
    metrics = stats.metrics()
    assert (metrics.followers, metrics.following) == (10, 3)
    assert (metrics.posts_scanned, metrics.views_scanned) == (2, 147)
    assert metrics.limited
    assert stats.reach == NOW - 100 * DAY  # capped: back to its oldest post


POSTS = [
    PostViews(views=100, posted_at=NOW - 1 * DAY),
    PostViews(views=40, posted_at=NOW - 10 * DAY),
    PostViews(views=7, posted_at=NOW - 100 * DAY),
]


@pytest.mark.parametrize(
    ("days", "views", "posts"),
    [(1, 100, 1), (7, 100, 1), (14, 140, 2), (365, 147, 3)],
)
def test_view_window_counts_posts_published_in_it(days, views, posts):
    window = ViewWindow.of(POSTS, days, NOW, covered_since=0)
    assert (window.days, window.views, window.posts) == (days, views, posts)
    assert window.covered


def test_capped_scan_does_not_cover_windows_it_did_not_reach():
    reach = NOW - 10 * DAY  # a capped read back to its oldest post
    assert ViewWindow.of(POSTS, 7, NOW, covered_since=reach).covered
    assert not ViewWindow.of(POSTS, 14, NOW, covered_since=reach).covered
    assert not ViewWindow.of(POSTS, 1, NOW, covered_since=None).covered
