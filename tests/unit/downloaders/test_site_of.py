"""`generic.site_of`: which yt-dlp extractor a link's site has, found from
the URL alone (no request), None when only Generic would try the page."""

import pytest

from ofmhelpers.downloaders.generic import site_of


@pytest.mark.parametrize(
    ("url", "site"),
    [
        ("https://www.instagram.com/reel/DepcsQDzDIT/", "Instagram"),
        ("https://www.instagram.com/p/DGUaB0Rtazx/", "Instagram"),
        ("https://www.tiktok.com/@some.one/video/7400000000000000000", "TikTok"),
        ("https://vm.tiktok.com/ZMabc123/", "vm.tiktok"),
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "youtube"),
        ("https://x.com/someone/status/1800000000000000000", "twitter"),
    ],
)
def test_a_video_site_names_its_extractor(url, site):
    assert site_of(url) == site


@pytest.mark.parametrize(
    "url",
    [
        "https://claude.ai/new",
        "https://5v7e0d9le0ndvx-8077.proxy.runpod.net/docs",
        "https://example.com/some/page",
    ],
)
def test_a_page_no_extractor_is_made_for_has_no_site(url):
    assert site_of(url) is None
