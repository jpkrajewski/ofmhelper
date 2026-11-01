"""A running job's card must never give up on it while the job runs: prod
2026-10-31, a phone put away mid-clone had no network, five status polls
failed and the card turned "Lost track"; the clone finished fine three
minutes later. In a real browser: failed polls are tried again until the
job answers, a hidden tab does not poll at all and catches up the moment
it is shown, and only a job that is gone ends the polling."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from playwright.sync_api import expect

if TYPE_CHECKING:
    from e2e_session import Session
    from playwright.sync_api import Route

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
