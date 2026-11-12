"""/download-assets "Download from Drive sheet" in a browser: the button
reads today's links from the person's sheet, their cards come, and it rests
(disabled, counting down in its label) after every click, also across a
reload; with nothing new it says so and starts nothing."""

import re

import pytest
from e2e_session import DEV_ROOT, Session, connect_google
from playwright.sync_api import expect

from ofmhelpers.integrations.gdrive.fake import FakeDrive, FakePersonDrive
from ofmhelpers.integrations.gdrive.models import SheetTab

pytestmark = pytest.mark.e2e

VA_ID = 2  # e2e conftest `_seed_users`
EMAIL = "va.google@example.com"
A = "https://www.tiktok.com/@user/video/111"
B = "https://www.tiktok.com/@user/video/222"
RESULT_TIMEOUT_MS = 30_000
LABEL = "Download from Drive sheet"


def _sheet(drive: FakeDrive, *links: str) -> None:
    """Today's column of the person's `content_links`, as typed on a phone."""
    from ofmhelpers.features.downloads import sheet

    FakePersonDrive(drive, EMAIL).children("root")
    mine = f"mydrive-{EMAIL}"
    found = drive.named(DEV_ROOT, mine)
    root = found[0] if found else drive.seed_folder(DEV_ROOT, mine, owner=EMAIL)
    tab = SheetTab(name="Sheet1", rows=[[sheet.today()], *([link] for link in links)])
    drive.add_sheet(root, "content_links", [tab])


def _rested() -> None:
    """A minute later."""
    from ofmhelpers.features.downloads import sheet

    sheet.Clicker(user_id=VA_ID).wake()


def test_the_button_downloads_todays_links_then_rests(
    va: Session, fake_drive: FakeDrive, jobs_idle
):
    connect_google(va, EMAIL)
    jobs_idle()
    _sheet(fake_drive, A, B)

    va.goto("/download-assets")
    page = va.page
    button = page.locator(".ph-toolbar .sheet-download")
    cards = page.locator("#results-panel .results > .result-item")
    status = page.locator("#results-panel .generation-status")
    expect(button).to_be_enabled()
    expect(button).to_have_text(LABEL)
    expect(page.locator(".ph-toolbar .hidden-toggle")).to_be_hidden()
    expect(cards).to_have_count(0)

    # The click: the page comes back with the download's cards, one a link.
    with page.expect_navigation():
        button.click()
    expect(cards.locator("video.result-video")).to_have_count(
        2, timeout=RESULT_TIMEOUT_MS
    )
    resting = re.compile(rf"^\s*{LABEL} \(\d+ s\)\s*$")
    expect(button).to_be_disabled()
    expect(button).to_have_text(resting)

    # Still resting after a reload.
    page.reload()
    expect(button).to_be_disabled()
    expect(button).to_have_text(resting)

    # Rested, nothing added: no new card, the page says so, and it rests again.
    _rested()
    page.reload()
    expect(button).to_be_enabled()
    expect(button).to_have_text(LABEL)
    button.click()
    expect(status).to_have_text(re.compile(r"^\s*No new links for \d\d/\d\d\s*$"))
    expect(button).to_be_disabled()
    expect(button).to_have_text(resting)
    expect(cards).to_have_count(2)

    # A click while it rests is refused by the server too.
    refused = page.request.post("/download-media/sheet-downloads")
    assert refused.status == 429
    assert re.fullmatch(r"Wait \d+ s", refused.json()["detail"])
    assert va.js_errors == []


def test_without_a_sheet_the_button_says_what_is_missing(va: Session):
    va.goto("/download-assets")
    page = va.page
    button = page.locator(".ph-toolbar .sheet-download")

    button.click()

    expect(page.locator("#results-panel .generation-status")).to_have_text(
        "Connect Google Drive on your Account page"
    )
    expect(button).to_be_enabled()
    expect(button).to_have_text(LABEL)
