"""The bio-classification rules -- the part of the lead hunt that decides
whether an account is a lead at all, and the part with no network in it."""

import pytest

from ofmhelpers.scraping.of_links import (
    find_aggregator_urls,
    find_direct_of_url,
    mentions_onlyfans,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("https://onlyfans.com/somegirl", "https://onlyfans.com/somegirl"),
        # Bare domains are how a bio actually reads -- no scheme, no www.
        ("onlyfans.com/somegirl 🔥", "https://onlyfans.com/somegirl"),
        ("www.onlyfans.com/somegirl", "https://onlyfans.com/somegirl"),
        # The official short domain and the regional one count as direct.
        ("of.link/somegirl", "https://of.link/somegirl"),
        ("onlyfans.co/somegirl", "https://onlyfans.co/somegirl"),
        # A link with trailing punctuation must not swallow it.
        ("see onlyfans.com/somegirl, dm me", "https://onlyfans.com/somegirl"),
        ("linktr.ee/somegirl", None),
        ("no links here at all", None),
    ],
)
def test_find_direct_of_url(text, expected):
    assert find_direct_of_url(text) == expected


def test_find_aggregator_urls_keeps_order():
    text = "beacons.ai/one and https://linktr.ee/two plus onlyfans.com/three"
    assert find_aggregator_urls(text) == [
        "https://beacons.ai/one",
        "https://linktr.ee/two",
    ]


@pytest.mark.parametrize(
    "text",
    ["OnlyFans in bio", "only fans 🔥", "0nlyf4ns", "my O.F is live", "onlyf4ns"],
)
def test_mentions_onlyfans_catches_the_obfuscations(text):
    assert mentions_onlyfans(text)


@pytest.mark.parametrize(
    "text",
    [
        # The whole reason the initialism is only matched dotted: a bare "of"
        # or "OF" is in half the English bios ever written, and every one of
        # them would become a lead.
        "photographer of light and shadow",
        "founder of a coffee brand",
        "PHOTOGRAPHER OF LIGHT AND SHADOW",
        "check my OF",
        "",
    ],
)
def test_mentions_onlyfans_ignores_the_english_word_of(text):
    assert not mentions_onlyfans(text)
