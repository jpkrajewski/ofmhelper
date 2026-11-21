import pytest

from ofmhelpers.integrations.downloaders.urls import NotALinkError, read_links

A = "https://www.instagram.com/reel/abc/"
B = "https://www.instagram.com/reel/def/"
BOM = chr(0xFEFF)
ZERO_WIDTH_SPACE = chr(0x200B)
NO_BREAK_SPACE = chr(0xA0)
RIGHT_TO_LEFT_MARK = chr(0x200F)
RIGHT_TO_LEFT_OVERRIDE = chr(0x202E)


@pytest.mark.parametrize(
    "pasted",
    [
        f"{A} {B}",
        f"{A}\t{B}",
        f"{A}\n\n{B}\n",
        f"{A}, {B};",
        f"{A} , {B}",
        f"{BOM}{A}\n{ZERO_WIDTH_SPACE}{B}",
        f"{A}{NO_BREAK_SPACE}{B}",
        f"{A}{RIGHT_TO_LEFT_MARK} {B}",
        f"{A}{RIGHT_TO_LEFT_OVERRIDE} {B}",
        f"{A}\x00{B}",
    ],
)
def test_links_are_read_whatever_separates_them(pasted):
    assert read_links(pasted) == [A, B]


@pytest.mark.parametrize(
    "link",
    [
        "https://en.wikipedia.org/wiki/Python_(programming_language)",
        "https://example.com/a,b",
        "https://x.com/don't",
        "https://example.com/clip.",
        "https://web.archive.org/web/2020/https://example.com/",
    ],
)
def test_a_link_keeps_every_character_it_needs(link):
    assert read_links(link) == [link]


def test_an_invisible_character_inside_a_link_is_dropped_not_split():
    assert read_links(f"https://www.insta{ZERO_WIDTH_SPACE}gram.com/reel/abc/") == [A]


@pytest.mark.parametrize(
    ("pasted", "link"),
    [
        ("instagram.com/reel/abc/", "https://instagram.com/reel/abc/"),
        ("instagram.com?igsh=1", "https://instagram.com?igsh=1"),
        ("EXAMPLE.COM/x", "https://EXAMPLE.COM/x"),
        ("instagram.com.", "https://instagram.com."),
        ("example.xn--p1ai/x", "https://example.xn--p1ai/x"),
        ("10.0.0.5/clip.mp4", "https://10.0.0.5/clip.mp4"),
        (
            "en.wikipedia.org/wiki/Help:Contents",
            "https://en.wikipedia.org/wiki/Help:Contents",
        ),
        ("example.com/go?u=https://x.com", "https://example.com/go?u=https://x.com"),
    ],
)
def test_a_bare_domain_gets_https(pasted, link):
    assert read_links(pasted) == [link]


@pytest.mark.parametrize(
    "pasted",
    [
        f"{A} see this",
        "21.11.2026",
        f"21.11 {A}",
        "v1.5",
        f"{A} e.g.",
        ".",
        "john@gmail.com",
    ],
)
def test_text_that_is_not_a_link_is_refused(pasted):
    with pytest.raises(NotALinkError):
        read_links(pasted)


@pytest.mark.parametrize("pasted", [" \n\t ", ", ;"])
def test_blank_text_has_no_links(pasted):
    assert read_links(pasted) == []
