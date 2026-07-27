import re
from unittest.mock import MagicMock

import pytest

from ofmhelpers.scraping.instagram_public import (
    _FOLLOWERS_RE,
    _VIEW_COUNT_RE,
    UNAVAILABLE_ERROR,
    _check_available,
    _check_not_blocked,
    _extract_count,
    is_unavailable_page,
    parse_count,
)


def test_parse_count_plain():
    assert parse_count("1,234", "") == 1234


def test_parse_count_k_suffix():
    assert parse_count("12.3", "K") == 12300


def test_parse_count_m_suffix():
    assert parse_count("1.2", "M") == 1200000


def test_extract_followers_from_og_description():
    text = "12.3K Followers, 456 Following, 789 Posts - See Instagram photos"
    assert _extract_count(_FOLLOWERS_RE, text) == 12300


def test_extract_count_returns_none_when_absent():
    assert _extract_count(_FOLLOWERS_RE, "nothing here") is None


def test_extract_views_from_reels_grid_icon_text():
    # exact text shape found on a live /<username>/reels/ grid item
    assert _extract_count(_VIEW_COUNT_RE, "View Count Icon10.2K") == 10200


def test_check_not_blocked_raises_on_blocked_page_title():
    page = MagicMock()
    page.title.return_value = "Page couldn't load • Instagram"
    with pytest.raises(RuntimeError, match="blocked"):
        _check_not_blocked(page)


def test_check_not_blocked_passes_on_normal_page_title():
    page = MagicMock()
    page.title.return_value = "jake_brooks_fd • Instagram photos and videos"
    _check_not_blocked(page)  # no raise


@pytest.mark.parametrize(
    "body",
    [
        # live wording, English -- Instagram renders a typographic apostrophe
        (
            "Sorry, this page isn\N{RIGHT SINGLE QUOTATION MARK}t available.\n"
            "The link you followed may be broken."
        ),
        # ... and a straight one has to match the same marker
        "Sorry, this page isn't available.",
        # ... and Polish, which is what a pl-locale container gets served
        (
            "Przepraszamy, ta strona jest niedostępna.\n"
            "Kliknięty link mógł być uszkodzony lub strona mogła zostać usunięta."
        ),
    ],
)
def test_banned_or_deleted_account_page_is_recognised(body):
    assert is_unavailable_page(body)


def test_live_profile_page_is_not_read_as_unavailable():
    assert not is_unavailable_page("jake_brooks_fd\n29K followers\nFollow")


def test_check_available_raises_so_the_sweep_records_why_it_went_dark():
    """A banned account answers 200 -- unchecked it scrapes as a live account
    with zero of everything."""
    page = MagicMock()
    page.inner_text.return_value = "Przepraszamy, ta strona jest niedostępna"
    with pytest.raises(RuntimeError, match=re.escape(UNAVAILABLE_ERROR)):
        _check_available(page)


def test_check_available_ignores_a_body_it_cannot_read():
    page = MagicMock()
    page.inner_text.side_effect = RuntimeError("no body yet")
    _check_available(page)  # no raise
