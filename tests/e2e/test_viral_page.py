# Copyright (C) 2026 jpkrajewski

import re

import pytest
from playwright.sync_api import expect
lazy from e2e_session import Session

from ofmhelpers.db.jobs.store import get_job

pytestmark = pytest.mark.e2e

RESULT_TIMEOUT_MS = 45_000


def test_a_profile_search_shows_its_top_reels_to_download_and_clone(va: Session):
    va.goto("/viral")
    va.page.fill("#viral-profile", "@sheep_love33")
    va.page.click('#viral-form button[type="submit"]')

    expect(va.page).to_have_url(re.compile(r"/viral\?job=\w+"))
    cards = va.page.locator(".generate-gallery-col .result-item")
    expect(cards.locator(".download-btn")).to_have_count(8, timeout=RESULT_TIMEOUT_MS)
    expect(cards.locator(".send-to .btn-segmented .send-to-clone")).to_have_count(8)
    expect(va.page.get_by_text("@sheep_love33 · top 8 of 8 reels")).to_be_visible()
    expect(cards.first.locator(".source")).to_have_text(re.compile(r"^3\.0M plays"))
    expect(va.page.locator(".viral-searches a")).to_have_text(["@sheep_love33"])

    with va.page.expect_download() as saved:
        va.page.get_by_role("link", name="Download all").click()
    assert saved.value.suggested_filename == "sheep_love33-top-8.zip"

    job = get_job(va.page.url.rsplit("job=", 1)[1])
    first_name = job.result[0]["name"]
    second_name = job.result[1]["name"]
    third_name = job.result[2]["name"]
    cards.nth(1).locator(".send-to .send-to-clone").click()
    va.page.wait_for_url("**/clone?video=*")
    assert second_name in va.page.url
    assert first_name not in va.page.url
    assert third_name not in va.page.url
    assert va.js_errors == []


def test_a_failed_clone_send_is_visible_and_can_be_retried(va: Session):
    va.goto("/viral")
    va.page.fill("#viral-profile", "@sheep_love33")
    va.page.click('#viral-form button[type="submit"]')
    button = va.page.locator(".generate-gallery-col .send-to-clone").first
    expect(button).to_be_visible(timeout=RESULT_TIMEOUT_MS)
    failures = 0

    def fail_send(route):
        nonlocal failures
        failures += 1
        if failures == 1:
            route.fulfill(status=500, body="broken")
        else:
            route.fulfill(status=200, content_type="text/html", body="<p>broken</p>")

    va.page.route("**/download-media/send", fail_send)
    button.click()
    expect(va.page.locator("#viral-send-status")).to_have_text(
        "Could not open the selected tool. Try again.",
    )
    expect(button).to_be_enabled()

    button.click()
    expect(va.page.locator("#viral-send-status")).to_have_text(
        "Could not open the selected tool. Try again.",
    )
    expect(button).to_be_enabled()

    va.page.unroute("**/download-media/send", fail_send)
    button.click()
    va.page.wait_for_url("**/clone?video=*")
    unexpected = []
    for error in va.js_errors:
        if (
            "Failed to load resource" in error
            or "not valid JSON" in error
            or "send to clone failed" in error
        ):
            continue
        unexpected.append(error)
    assert unexpected == []


def test_text_that_is_no_profile_is_refused_in_place(va: Session):
    va.goto("/viral")
    va.page.fill("#viral-profile", "not a profile!!")
    va.page.click('#viral-form button[type="submit"]')

    expect(va.page.locator("#viral-error")).to_have_text(
        "Paste an Instagram profile: @name or its link",
    )
    assert va.page.url.endswith("/viral")
