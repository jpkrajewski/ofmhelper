"""Parsing only, fully offline: Instagram's answers in tests/fixtures/scraper
read by `instagram.pages` (HTML) and `instagram.models` (GraphQL/REST JSON),
noise and all."""

import pytest
from repo_paths import FIXTURES

from ofmhelpers.instagram import pages
from ofmhelpers.instagram.models import (
    GLOBAL_CDN_HOST,
    ClipsPage,
    MediaInfo,
    MediaPage,
    ProfilePage,
    ReelMedia,
    TimelinePage,
)

SCRAPER = FIXTURES / "scraper"


def _text(name: str) -> str:
    return (SCRAPER / name).read_text(encoding="utf-8")


def test_reels_page_gives_the_profile_id_and_lsd():
    page = pages.reels_page(_text("reels_page.html"))
    assert page.profile_id == "41000000001"
    assert page.lsd == "AVqZ2hT0lsd"


def test_a_withheld_reels_page_has_no_profile_id():
    page = pages.reels_page(_text("reels_page_withheld.html"))
    assert page.profile_id is None
    assert page.lsd == "AVqWithheld"


def test_a_logged_out_page_has_no_login_tokens():
    assert pages.login_tokens(_text("reels_page.html"), referer="r") is None


def test_the_logged_in_page_gives_the_login_tokens():
    tokens = pages.login_tokens(_text("reels_page_logged_in.html"), referer="r")
    assert tokens is not None
    assert tokens.actor_id == "17841400000000000"
    assert tokens.fb_dtsg == "NAcMdtsgToken:17:1729500000"
    assert tokens.lsd == "AVqLoggedIn"
    assert tokens.referer == "r"


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("reels_page.html", True),
        ("page_unavailable.html", False),
        ("crawler_missing.html", None),
    ],
)
def test_which_page_instagram_rendered(name, kind):
    assert pages.is_profile_page(_text(name)) is kind


def test_the_crawler_view_tells_a_profile_from_a_missing_one():
    assert pages.crawler_sees_a_profile(_text("crawler_profile.html"))
    assert not pages.crawler_sees_a_profile(_text("crawler_missing.html"))


def test_profile_query():
    user = ProfilePage.model_validate_json(_text("profile_query.json")).data.user
    assert user is not None
    assert (user.pk, user.username, user.follower_count, user.following_count) == (
        "41000000001",
        "sheep_love33",
        12034,
        180,
    )
    assert not user.is_private
    assert user.avatar_url.endswith("s320x320")


def test_reels_tab_page():
    page = ClipsPage.model_validate_json(_text("clips_page.json"))
    assert [(r.code, r.play_count, r.pinned) for r in page.reels] == [
        ("DTjqfake001", 1500321, True),
        ("DTjqfake002", 61002, False),
    ]
    assert page.reels[0].image_versions2.first.endswith("dst-jpg_e15")
    assert page.next_cursor == "QVFDcursor2"


def test_timeline_page_videos_have_an_mp4_and_a_length_photos_none():
    page = TimelinePage.model_validate_json(_text("timeline_page.json"))
    media = {n.code: ReelMedia.from_dash(n) for n in page.nodes}
    assert media["DTjqphoto01"] is None
    first, second = media["DTjqfake001"], media["DTjqfake002"]
    assert first is not None
    assert second is not None
    assert first.duration_s == 9.5
    assert second.duration_s == 64.5
    # Regional hosts are moved onto the global one.
    assert first.video_url.startswith(f"https://{GLOBAL_CDN_HOST}/")
    assert page.next_cursor is None


def test_a_hidden_accounts_timeline_is_empty_not_an_error():
    page = TimelinePage.model_validate_json(_text("timeline_hidden_account.json"))
    assert page.nodes == []
    assert page.next_cursor is None


def test_gated_media_reads_as_none():
    page = MediaPage.model_validate_json(_text("media_gated.json"))
    assert ReelMedia.from_page(page) is None


def test_logged_in_media_info():
    media = ReelMedia.from_info(
        MediaInfo.model_validate_json(_text("media_info_logged_in.json"))
    )
    assert media is not None
    assert media.duration_s == 13.2
    assert media.taken_at == 1792500000
    assert media.video_url.startswith(f"https://{GLOBAL_CDN_HOST}/")
