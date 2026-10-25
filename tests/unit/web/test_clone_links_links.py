"""/clone-links Check: the pasted text read as one link per line -- cleaned,
each once, in the order pasted -- and every line that is not exactly one
link named."""

import pytest

from ofmhelpers.web.clone_links.links import SourceLink, read_links

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


def test_instagram_posts_and_tv_keep_their_kind():
    assert (
        SourceLink(url="https://www.instagram.com/p/XYZ/?img_index=2").url
        == "https://www.instagram.com/p/XYZ/"
    )
    assert (
        SourceLink(url="instagram.com/tv/QQQ").url
        == "https://www.instagram.com/tv/QQQ/"
    )


def test_a_tiktok_video_keeps_only_its_path():
    found = SourceLink(
        url="https://www.tiktok.com/@some.one/video/7400000000000000000?is_from_webapp=1&sender_device=pc&lang=en"
    )
    assert found.url == "https://www.tiktok.com/@some.one/video/7400000000000000000"


def test_other_sites_lose_only_trackers_and_the_fragment():
    found = SourceLink(
        url="https://www.youtube.com/watch?v=dQw4w9WgXcQ&si=abc&utm_campaign=x#t=10"
    )
    assert found.url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def test_a_short_link_is_kept_as_it_is():
    assert SourceLink(url="https://vm.tiktok.com/ZMabc123/").url == (
        "https://vm.tiktok.com/ZMabc123/"
    )


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
