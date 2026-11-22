import pytest
from e2e_session import Session, connect_google, my_root
from playwright.sync_api import Request, expect

from ofmhelpers.config.types import AccountFolder
from ofmhelpers.db.devices import store as devices
from ofmhelpers.db.devices.models import DeviceFields, LoginFields
from ofmhelpers.integrations.storage.gdrive.fake import FakeDrive

pytestmark = pytest.mark.e2e

VA_ID = 2
GOOGLE = "va.google@example.com"
MB = 1_000_000


def _trials(va: Session, drive: FakeDrive, jobs_idle) -> list[str]:
    connect_google(va, GOOGLE)
    jobs_idle()
    phone = devices.add_device(
        DeviceFields(
            label="Vee 01", model_id="iphone-13", color_id="blue", owner="Vee"
        ),
        user_id=VA_ID,
    )
    assert devices.add_login(phone, LoginFields(service="instagram", username="vee.ig"))
    va.goto("/drive")
    with va.page.expect_navigation():
        va.page.click("#dr-sync")
    jobs_idle()
    (account,) = drive.named("vee.ig - Vee", my_root(drive, GOOGLE))
    trials = drive.seed_folder(AccountFolder.TRIALS, account, owner=GOOGLE)
    return [
        drive.add_file(trials, f"VID_100{i}.mp4", "video/mp4", owner=GOOGLE, size=MB)
        for i in range(2)
    ]


def _scanned(va: Session, jobs_idle) -> None:
    va.goto("/storage")
    jobs_idle()
    va.goto("/storage")
    expect(va.page.locator(".st-table")).to_contain_text("vee.ig - Vee")


def test_a_folders_trials_go_to_the_bin_after_a_confirm(
    va: Session, fake_drive: FakeDrive, jobs_idle
):
    files = _trials(va, fake_drive, jobs_idle)
    _scanned(va, jobs_idle)
    page = va.page
    browser_popups: list[str] = []
    page.on("dialog", lambda dialog: browser_popups.append(dialog.message))

    page.locator('.st-table .st-bin[data-part="TRIALS"]').click()
    expect(page.locator("#st-confirm")).to_be_visible()
    expect(page.locator("#st-confirm-text")).to_have_text(
        "Trials of vee.ig - Vee · 2.0 MB"
    )
    with page.expect_navigation():
        page.locator("#st-confirm-go").click()
    jobs_idle()
    va.goto("/storage")

    assert browser_popups == []
    expect(page.locator("#main")).to_contain_text("Moved 2 items (2.0 MB) of trials")
    for file_id in files:
        found = fake_drive.item(file_id)
        assert found is not None
        assert found.trashed
    assert va.js_errors == []


def test_cancel_moves_nothing(va: Session, fake_drive: FakeDrive, jobs_idle):
    files = _trials(va, fake_drive, jobs_idle)
    _scanned(va, jobs_idle)
    page = va.page
    posts: list[str] = []

    def sent(request: Request) -> None:
        if request.method == "POST" and "/storage/bin-moves" in request.url:
            posts.append(request.url)

    page.on("request", sent)

    page.locator(".toolbar-actions .st-bin").first.click()
    page.locator("#st-confirm-back").click()
    expect(page.locator("#st-confirm")).to_be_hidden()
    page.locator(".toolbar-actions .st-bin").first.click()
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)

    expect(page.locator("#st-confirm")).to_be_hidden()
    assert posts == []
    for file_id in files:
        found = fake_drive.item(file_id)
        assert found is not None
        assert not found.trashed
    assert va.js_errors == []


def test_an_html_answer_shows_plain_words_and_the_buttons_come_back(
    va: Session, fake_drive: FakeDrive, jobs_idle
):
    _trials(va, fake_drive, jobs_idle)
    _scanned(va, jobs_idle)
    page = va.page
    page.route(
        "**/storage/bin-moves",
        lambda route: route.fulfill(
            status=200, content_type="text/html", body="<!doctype html><p>login</p>"
        ),
    )

    page.locator(".toolbar-actions .st-bin").first.click()
    page.locator("#st-confirm-go").click()

    expect(page.locator("#st-status")).to_have_text("Could not start. Try again.")
    expect(page.locator(".toolbar-actions .st-bin").first).to_be_enabled()
    assert va.js_errors == []
