"""Instagram, logged out: what the scans read, on the fake and through the
real Instagram (direct, then each configured proxy -- like the app, a rate
limit or a "not found" for an account that exists means a flagged IP)."""

import itertools
import time
import uuid

import pytest

from ofmhelpers.config import settings
from ofmhelpers.instagram import (
    InstagramClient,
    InstagramRateLimitedError,
    ProfileNotFoundError,
    scan_profile,
)
from ofmhelpers.instagram.models import GLOBAL_CDN_HOST, Profile

# A public profile with reels that will not disappear, per backend.
PROFILE = {"fake": "sheep_love33", "real": "instagram"}
_INSTAGRAM_LAUNCH = 1286323200  # 2010-10-06


def _open(username: str) -> tuple[InstagramClient, Profile]:
    routes = [None, *settings.instagram.proxy_urls]
    for proxy in routes:
        client = InstagramClient(proxy)
        try:
            return client, client.open_profile(username)
        except (InstagramRateLimitedError, ProfileNotFoundError):
            continue
    pytest.fail(f"Instagram refused all {len(routes)} routes")


def test_a_public_profile_has_followers(backend):
    _, profile = _open(PROFILE[backend])
    assert profile.username == PROFILE[backend]
    assert profile.followers > 0
    assert profile.user_id.isdigit()
    assert not profile.is_private


def test_its_reels_tab_lists_codes_views_and_post_times(backend):
    client, profile = _open(PROFILE[backend])
    reels = list(itertools.islice(client.reels(profile.user_id), 5))
    assert reels
    for reel in reels:
        assert reel.code
        assert reel.play_count is not None
        assert _INSTAGRAM_LAUNCH < reel.posted_at <= time.time() + 60


def test_a_scan_gets_mp4s_with_a_length_on_the_global_cdn(backend):
    """What /reels and /accounts need of a profile: its recent reels' mp4s
    and lengths -- from the posts timeline when Instagram answers it, else
    reel by reel (it throttles the logged-out posts query on its own,
    answering 200 with "Rate limit exceeded": 2026-10-23)."""
    client, profile = _open(PROFILE[backend])
    found = scan_profile(
        client,
        profile.username,
        min_views=0,
        max_duration_s=3600,
        max_age_s=10 * 365 * 86400,
        limit=3,
    )
    videos = [r for r in found.reels if r.video_url]
    assert videos
    assert all(f"//{GLOBAL_CDN_HOST}/" in str(r.video_url) for r in videos)
    assert all((r.duration_s or 0) > 0 for r in videos)


def test_a_missing_profile_is_not_found(backend):
    missing = f"nosuchuser{uuid.uuid4().hex[:12]}"
    with pytest.raises((ProfileNotFoundError, InstagramRateLimitedError)):
        InstagramClient().open_profile(missing)


@pytest.mark.live
def test_canary_a_public_account_through_a_real_proxy(real_env):
    """The scans' route: Instagram through one of the configured proxies
    (OFM_IG_PROXIES), never direct."""
    proxies = settings.instagram.proxy_urls
    assert proxies, "no OFM_IG_PROXIES in .env"
    for proxy in proxies:
        try:
            profile = InstagramClient(proxy).open_profile(PROFILE["real"])
        except (InstagramRateLimitedError, ProfileNotFoundError):
            continue
        assert profile.followers > 0
        return
    pytest.fail(f"Instagram refused all {len(proxies)} proxies")
