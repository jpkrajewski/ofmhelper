"""/clone-links Check: the pasted text read as one link per line -- cleaned,
each once, in the order pasted -- and every line that is not exactly one
link named."""

import pytest

from ofmhelpers.downloaders.generic import site_of
from ofmhelpers.web.clone_links.links import (
    NOT_A_VIDEO_SITE,
    NOT_ONE_POST,
    SHARE_LINK,
    SourceLink,
    read_links,
    read_pasted,
    site_name,
)

REEL = "https://www.instagram.com/reel/ABC_12-3/"


@pytest.mark.parametrize(
    "pasted",
    [
        "https://www.instagram.com/reel/ABC_12-3/?igsh=MWQ1ZGUxMzBkMA==",
        "https://instagram.com/reel/ABC_12-3",
        "instagram.com/reels/ABC_12-3/",
        "https://www.instagram.com/some.user/reel/ABC_12-3/?utm_source=ig_web",
        "HTTPS://WWW.INSTAGRAM.COM/reel/ABC_12-3/#comments",
        "https://www.instagram.com/reel/ABC_12-3/).",
    ],
)
def test_every_spelling_of_one_instagram_reel_is_one_link(pasted):
    assert SourceLink(url=pasted).url == REEL


@pytest.mark.parametrize(
    "pasted",
    [
        "https://www.instagram.com/p/ABC_12-3/?img_index=2",
        "instagram.com/tv/ABC_12-3",
        "https://www.instagram.com/some.user/p/ABC_12-3/",
        "http://instagram.com/reels/ABC_12-3",
    ],
)
def test_every_kind_of_instagram_post_is_written_as_its_reel(pasted):
    """One shortcode is one post: /p/, /tv/ and /reel/ of it are one run."""
    assert SourceLink(url=pasted).url == REEL


def test_a_post_pasted_as_p_and_as_reel_is_one_link():
    found = read_links(
        "https://www.instagram.com/p/ABC_12-3/\n"
        "https://www.instagram.com/reel/ABC_12-3/?igsh=x"
    )

    assert found.links == [REEL]
    assert found.repeated == 1
    assert found.bad_lines == []


def test_a_reels_audio_page_is_not_made_a_reel():
    found = SourceLink(url="https://www.instagram.com/reels/audio/123456/")
    assert found.url == "https://www.instagram.com/reels/audio/123456/"


def test_a_tiktok_video_keeps_only_its_path():
    found = SourceLink(
        url="https://www.tiktok.com/@some.one/video/7400000000000000000?is_from_webapp=1&sender_device=pc&lang=en"
    )
    assert found.url == "https://www.tiktok.com/@some.one/video/7400000000000000000"


@pytest.mark.parametrize(
    "pasted",
    [
        "tiktok.com/@a/video/1",
        "https://m.tiktok.com/@a/video/1?x=1",
        "https://www.tiktok.com/@a/video/1/",
        "HTTP://TIKTOK.COM/@a/video/1#c",
    ],
)
def test_every_host_of_one_tiktok_video_is_its_www_page(pasted):
    """yt-dlp takes only www.tiktok.com: the bare and m. hosts were refused
    as no video site."""
    assert SourceLink(url=pasted).url == "https://www.tiktok.com/@a/video/1"


def test_tiktok_spellings_are_one_link_yt_dlp_takes():
    found = read_pasted(
        "tiktok.com/@a/video/7400000000000000000\n"
        "https://m.tiktok.com/@a/video/7400000000000000000?x=1\n"
        "https://www.tiktok.com/@a/video/7400000000000000000"
    )

    assert found.links == ["https://www.tiktok.com/@a/video/7400000000000000000"]
    assert found.repeated == 2
    assert found.bad_lines == []
    assert site_of(found.links[0]) == "TikTok"


def test_other_sites_lose_only_trackers_and_the_fragment():
    found = SourceLink(
        url="https://www.youtube.com/watch?v=dQw4w9WgXcQ&si=abc&utm_campaign=x#t=10"
    )
    assert found.url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def test_a_short_link_is_kept_as_it_is():
    assert SourceLink(url="https://vm.tiktok.com/ZMabc123/").url == (
        "https://vm.tiktok.com/ZMabc123/"
    )
    assert SourceLink(url="https://vt.tiktok.com/ZSabc123/?_r=1").url == (
        "https://vt.tiktok.com/ZSabc123/"
    )


@pytest.mark.parametrize(
    "pasted",
    [
        "https://www.instagram.com/share/reel/BAHxyz123/",
        "https://www.instagram.com/share/p/BAHxyz123/",
        "https://www.instagram.com/share/BAHxyz123/",
        "instagram.com/share/reel/BAHxyz123",
        "https://www.facebook.com/share/r/abcDEF123/",
    ],
)
def test_a_share_link_is_refused_never_made_a_reel(pasted):
    """A share token is no shortcode: rewritten to /reel/<token>/ it passed
    Check and then failed as "wants a login"."""
    found = read_pasted(pasted)

    assert found.links == []
    assert [(b.number, b.reason) for b in found.bad_lines] == [(1, SHARE_LINK)]
    assert "/share/" in SourceLink(url=pasted).url


@pytest.mark.parametrize(
    "pasted",
    [
        "https://www.instagram.com/reel/ABC_12-3/",
        "https://www.instagram.com/p/ABC_12-3/",
        "https://www.tiktok.com/@some.one/video/7400000000000000000",
        "https://vm.tiktok.com/ZMabc123/",
        "https://vt.tiktok.com/ZSabc123/",
        "https://www.youtube.com/shorts/dQw4w9WgXcQ",
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtu.be/dQw4w9WgXcQ",
        "https://x.com/user/status/1234567890123456789",
        "https://twitter.com/user/status/1234567890123456789",
        "https://www.facebook.com/reel/123456789012345",
        "https://www.facebook.com/watch/?v=123456789012345",
    ],
)
def test_one_post_of_a_video_site_is_taken(pasted):
    found = read_pasted(pasted)
    assert found.bad_lines == []
    assert len(found.links) == 1


@pytest.mark.parametrize(
    "pasted",
    [
        "https://www.tiktok.com/@someuser",
        "https://www.tiktok.com/tag/cats",
        "https://www.tiktok.com/music/song-123456",
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLabcdefghijklmnop",
        "https://youtu.be/dQw4w9WgXcQ?list=PLabcdefghijklmnop",
        "https://www.youtube.com/@someone/shorts",
        "https://www.youtube.com/playlist?list=PLabcdefghijklmnop",
        "https://www.instagram.com/someuser/",
        "https://www.instagram.com/stories/someuser/123456/",
        "https://www.instagram.com/explore/tags/cats/",
        "https://t.co/abc123",
        "https://www.facebook.com/flx/warn/?u=http%3A%2F%2Fpostgres%3A5432%2F",
    ],
)
def test_a_profile_playlist_or_redirector_is_refused(pasted):
    """Only single-post extractors: a list link fanned out into every entry
    (preview and download), and the clone was made of entry 1."""
    found = read_pasted(pasted)

    assert found.links == []
    assert [(b.number, b.reason) for b in found.bad_lines] == [(1, NOT_ONE_POST)]


def test_a_link_of_no_site_says_which_sites():
    found = read_pasted("https://abc-8077.proxy.runpod.net/docs")
    assert [b.reason for b in found.bad_lines] == [NOT_A_VIDEO_SITE]


def test_the_extractors_taken_are_the_settings(monkeypatch):
    monkeypatch.setenv("OFM_CLONE_LINKS_VIDEO_EXTRACTORS", '["Instagram"]')

    found = read_pasted(f"{REEL}\nhttps://www.tiktok.com/@a/video/1")

    assert found.links == [REEL]
    assert [(b.number, b.reason) for b in found.bad_lines] == [(2, NOT_ONE_POST)]


@pytest.mark.parametrize(
    ("url", "name"),
    [
        (REEL, "Instagram"),
        ("https://vm.tiktok.com/ZMabc123/", "TikTok"),
        ("https://youtu.be/dQw4w9WgXcQ", "YouTube"),
        ("https://m.youtube.com/shorts/dQw4w9WgXcQ", "YouTube"),
        ("https://x.com/u/status/1", "X"),
        ("https://twitter.com/u/status/1", "Twitter"),
        ("https://m.facebook.com/reel/1", "Facebook"),
    ],
)
def test_a_site_is_named_as_a_va_names_it(url, name):
    assert site_name(url) == name


@pytest.mark.parametrize("text", ["hello", "e.g.", "ftp://files.example.com/a", "1.5"])
def test_what_is_no_web_link_is_refused(text):
    with pytest.raises(ValueError, match="Not a link"):
        SourceLink(url=text)


def test_read_links_takes_one_link_per_line_in_order_once_each():
    pasted = f"""{REEL}?igsh=1

    instagram.com/reels/ABC_12-3,
    https://www.tiktok.com/@a/video/1?_r=1"""

    found = read_links(pasted)

    assert found.links == [REEL, "https://www.tiktok.com/@a/video/1"]
    assert found.repeated == 1
    assert found.bad_lines == []


def test_a_line_that_is_not_exactly_one_link_is_named_never_split():
    glued = f"{REEL}https://www.instagram.com/reel/C9BXX__oGuy/?utm_source=x"
    pasted = f"{REEL}\n{glued}\n{REEL} {REEL}\n{REEL} thanks\nthanks!"

    found = read_links(pasted)

    assert found.links == [REEL]
    assert [(b.number, b.text, b.reason) for b in found.bad_lines] == [
        (2, glued, "2 links on one line"),
        (3, f"{REEL} {REEL}", "2 links on one line"),
        (4, f"{REEL} thanks", "a link and other text"),
        (5, "thanks!", "not a link"),
    ]


def test_a_link_of_no_video_site_is_a_bad_line_too():
    docs = "https://abc-8077.proxy.runpod.net/docs#/default/post"
    pasted = f"2. dsadasdasd\n{REEL}\n{docs}"

    found = read_links(pasted, video_site=lambda url: "instagram.com" in url)

    assert found.links == [REEL]
    assert [(b.number, b.reason) for b in found.bad_lines] == [
        (1, "not a link"),
        (3, "Please put Instagram, TikTok, Twitter etc."),
    ]


def test_a_link_carried_in_a_query_stays_part_of_its_link():
    redirect = "https://l.example.com/out?u=https://www.instagram.com/reel/X1/"

    found = read_links(redirect)

    assert found.links == [SourceLink(url=redirect).url]
    assert found.links[0].startswith("https://l.example.com/out?u=")


def test_nothing_pasted_is_no_links():
    found = read_links("  \n ")
    assert found.links == []
    assert found.bad_lines == []
    assert found.repeated == 0
