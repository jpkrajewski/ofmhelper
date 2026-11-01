"""/viral in a real browser: a profile in, the page waits on the search and
shows the profile's reels most played first, each with Download, all of
them as one zip (Apify faked: apify/fake.py serves the Instagram fake's
sheep_love33, 8 reels, the most played 3M)."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import pytest
from playwright.sync_api import expect

if TYPE_CHECKING:
    from e2e_session import Session

pytestmark = pytest.mark.e2e

# The search runs on the worker; the page polls every 3 s.
RESULT_TIMEOUT_MS = 45_000


def test_a_profile_search_shows_its_top_reels_to_download(va: Session):
    va.goto("/viral")
    va.page.fill("#viral-profile", "@sheep_love33")
    va.page.click('#viral-form button[type="submit"]')

    expect(va.page).to_have_url(re.compile(r"/viral\?job=\w+"))
    cards = va.page.locator(".generate-gallery-col .result-item")
    expect(cards.locator(".download-btn")).to_have_count(8, timeout=RESULT_TIMEOUT_MS)
    expect(va.page.get_by_text("@sheep_love33 · top 8 of 8 reels")).to_be_visible()
    expect(cards.first.locator(".source")).to_have_text(re.compile(r"^3\.0M plays"))
    expect(va.page.locator(".viral-searches a")).to_have_text(["@sheep_love33"])

    with va.page.expect_download() as saved:
        va.page.get_by_role("link", name="Download all").click()
    assert saved.value.suggested_filename == "sheep_love33-top-8.zip"
    assert va.js_errors == []


def test_text_that_is_no_profile_is_refused_in_place(va: Session):
    va.goto("/viral")
    va.page.fill("#viral-profile", "not a profile!!")
    va.page.click('#viral-form button[type="submit"]')

    expect(va.page.locator("#viral-error")).to_have_text(
        "Paste an Instagram profile: @name or its link"
    )
    assert va.page.url.endswith("/viral")
