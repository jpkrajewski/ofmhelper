"""A running job's card must never give up on it while the job runs: prod
2026-10-31, a phone put away mid-clone had no network, five status polls
failed and the card turned "Lost track"; the clone finished fine three
minutes later. In a real browser: failed polls are tried again until the
job answers, a hidden tab does not poll at all and catches up the moment
it is shown, and only a job that is gone ends the polling.

Nor may a run vanish while its files go up: prod 2026-11-06, a picture on a
slow uplink still uploading after 80 s showed "generating…", a refresh and
it was gone (nginx `400 0`, the app never saw it). The card shows the
upload, and leaving the page meanwhile asks first."""

from __future__ import annotations

import os
import re
from typing import TYPE_CHECKING

import pytest
from PIL import Image
from playwright.sync_api import expect

if TYPE_CHECKING:
    from pathlib import Path

    from e2e_session import Session
    from playwright.sync_api import Dialog, Route

pytestmark = pytest.mark.e2e

STATUS = "**/jobs/*/status"
# More failed polls in a row than the card used to survive (5).
FAILED_POLLS = 6
# Polls retry every 2 s: six failed ones, then the answer.
RESULT_TIMEOUT_MS = 45_000
HIDE = """() => {
    Object.defineProperty(document, "hidden", { configurable: true, get: () => true });
    document.dispatchEvent(new Event("visibilitychange"));
}"""
SHOW = """() => {
    Object.defineProperty(document, "hidden", { configurable: true, get: () => false });
    document.dispatchEvent(new Event("visibilitychange"));
}"""


def _generate(session: Session, delay_s: int) -> str:
    """A fake image generation from the open /generate; the card's selector."""
    session.page.select_option("#provider-select", "fake")
    session.page.select_option("#model-select", "fake:image")
    session.page.fill('#model-fields [name="delay"]', str(delay_s))
    session.page.fill('textarea[name="prompt"]', "e2e: a lost connection")
    with session.page.expect_response(lambda r: r.url.endswith("/ai/run")) as run:
        session.page.click('#generate-form button[type="submit"]')
    return f'.result-item[data-job-id="{run.value.json()["job_id"]}"]'


def _page_errors(session: Session) -> list[str]:
    """JS errors, minus the browser's own line for each failed request."""
    return [e for e in session.js_errors if "Failed to load resource" not in e]


def test_failed_polls_are_retried_until_the_job_answers(va: Session):
    failed: list[str] = []

    def offline_at_first(route: Route) -> None:
        if len(failed) < FAILED_POLLS:
            failed.append(route.request.url)
            route.abort("internetdisconnected")
        else:
            route.continue_()

    va.page.route(STATUS, offline_at_first)
    va.goto("/generate")
    card = _generate(va, delay_s=0)

    expect(va.page.locator(f"{card} .download-btn")).to_be_visible(
        timeout=RESULT_TIMEOUT_MS
    )
    assert len(failed) == FAILED_POLLS
    expect(va.page.get_by_text("Lost track")).to_have_count(0)
    assert _page_errors(va) == []


def test_a_hidden_tab_does_not_poll_and_catches_up_when_shown(va: Session):
    polls: list[str] = []
    va.page.on("request", lambda r: "/status" in r.url and polls.append(r.url))
    va.goto("/generate")
    va.page.evaluate(HIDE)

    card = _generate(va, delay_s=1)
    va.page.wait_for_timeout(5_000)  # the job finishes meanwhile
    assert polls == []
    expect(va.page.locator(f"{card} .spinner")).to_have_count(1)

    va.page.evaluate(SHOW)
    expect(va.page.locator(f"{card} .download-btn")).to_be_visible(
        timeout=RESULT_TIMEOUT_MS
    )
    assert _page_errors(va) == []


WAIT = "Images/videos are still uploading to server. Wait till they are uploaded."


def _slow_generate(va: Session, tmp_path: Path) -> None:
    """A fake generation from /generate with a ~1.9 MB picture (random pixels
    do not compress) through a 128 KB/s uplink: ~15 s of upload. The browser's
    own leave box, when it comes, is answered Stay."""
    side = 800
    picture = tmp_path / "big.png"
    Image.frombytes("RGB", (side, side), os.urandom(side * side * 3)).save(picture)
    va.goto("/generate")
    va.page.select_option("#provider-select", "fake")
    va.page.select_option("#model-select", "fake:image")
    va.page.fill('#model-fields [name="delay"]', "0")
    va.page.fill('textarea[name="prompt"]', "e2e: a slow uplink")
    va.page.locator(
        '#model-fields .file-picker[data-field="reference_image_urls"] .file-add-input'
    ).set_input_files(str(picture))
    cdp = va.context.new_cdp_session(va.page)
    cdp.send(
        "Network.emulateNetworkConditions",
        {
            "offline": False,
            "latency": 0,
            "downloadThroughput": -1,
            "uploadThroughput": 128 * 1024,
        },
    )

    def stay(dialog: Dialog) -> None:
        dialog.dismiss()

    va.page.on("dialog", stay)
    va.page.click('#generate-form button[type="submit"]')
    uploading = va.page.locator(".result-item", has=va.page.locator("progress"))
    expect(uploading.locator(".source")).to_have_text(
        re.compile(r"Uploading… \d{1,2}%")
    )


def test_a_slow_upload_shows_its_progress_asks_before_leaving_and_becomes_the_job(
    va: Session, tmp_path: Path
):
    with va.page.expect_response(
        lambda r: r.url.endswith("/ai/run"), timeout=RESULT_TIMEOUT_MS
    ) as run:
        _slow_generate(va, tmp_path)
        expect(va.page.get_by_text("generating…")).to_have_count(0)
        expect(va.page.locator(".upload-wait")).to_have_text(WAIT)
        # A link away: the page's own box, in these words; Wait = stay.
        va.page.click('.site-header nav a[href="/clone"]')
        leave = va.page.locator("#upload-leave")
        expect(leave).to_be_visible()
        expect(leave.get_by_role("heading")).to_have_text("Leave site?")
        expect(leave.locator("p")).to_have_text(WAIT)
        leave.get_by_role("button", name="Wait").click()
        expect(leave).to_be_hidden()
        assert va.page.url.endswith("/generate")
        # A refresh or a closed tab: the browser's own box; dismissed = stay.
        with va.page.expect_event("dialog") as leaving:
            va.page.close(run_before_unload=True)
        assert leaving.value.type == "beforeunload"
    card = f'.result-item[data-job-id="{run.value.json()["job_id"]}"]'

    expect(va.page.locator(f"{card} .download-btn")).to_be_visible(
        timeout=RESULT_TIMEOUT_MS
    )
    expect(va.page.locator("progress")).to_have_count(0)
    expect(va.page.locator(".upload-wait")).to_be_hidden()
    assert _page_errors(va) == []


def test_leave_in_the_box_goes_there_without_asking_again(va: Session, tmp_path):
    asked: list[str] = []
    va.page.on("dialog", lambda dialog: asked.append(dialog.type))
    _slow_generate(va, tmp_path)

    va.page.click('.site-header nav a[href="/clone"]')
    va.page.locator("#upload-leave").get_by_role("button", name="Leave").click()

    expect(va.page).to_have_url(re.compile(r"/clone$"))
    assert asked == []


def test_a_job_that_is_gone_ends_its_polling(va: Session):
    polls: list[str] = []

    def gone(route: Route) -> None:
        polls.append(route.request.url)
        route.fulfill(status=404, json={"detail": "Job not found"})

    va.page.route(STATUS, gone)
    va.goto("/generate")
    _generate(va, delay_s=0)

    expect(va.page.get_by_text("Not found")).to_be_visible(timeout=RESULT_TIMEOUT_MS)
    va.page.wait_for_timeout(3_000)  # past the next poll it would have made
    assert len(polls) == 1
    assert _page_errors(va) == []
