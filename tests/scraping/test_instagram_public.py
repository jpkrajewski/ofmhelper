from unittest.mock import MagicMock

import pytest

from ofmhelpers.scraping.instagram_public import (
    _FOLLOWERS_RE,
    _VIEW_COUNT_RE,
    _check_not_blocked,
    _extract_count,
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
