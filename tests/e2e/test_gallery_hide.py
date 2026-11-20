lazy from pathlib import Path

import pytest
from e2e_session import Session
from playwright.sync_api import expect

lazy from ofmhelpers.db.jobs.store import create_job
lazy from ofmhelpers.features.jobs.runner import run_job

pytestmark = pytest.mark.e2e

VA_ID = 2

PAGES = [
    ("/generate", "fake_ai"),
    ("/clone", "clone"),
    ("/clone-long", "clone_long"),
    ("/download-assets", "download_media"),
]


def _done(task: str, out: Path) -> str:

    out.write_bytes(b"png")
    job_id = create_job(task, {"prompt": out.stem}, actor=str(VA_ID))
    run_job(job_id, lambda: [{"name": out.name, "path": str(out)}], {})
    return job_id


def _failed(task: str) -> str:

    job_id = create_job(task, {"prompt": "failed"}, actor=str(VA_ID))

    def boom():
        msg = "Wrong API Key"
        raise RuntimeError(msg)

    run_job(job_id, boom, {})
    return job_id


def _pick(card, item: str) -> None:
    card.locator(".menu summary").click()
    card.locator(item).click()


@pytest.mark.parametrize(("path", "task"), PAGES, ids=[p for p, _ in PAGES])
def test_hide_show_hidden_unhide(va: Session, tmp_path: Path, path: str, task: str):
    kept = _done(task, tmp_path / "kept.png")
    shown = _done(task, tmp_path / "shown.png")
    failed = _failed(task)

    va.goto(path)
    page = va.page
    tools = page.locator(".ph-toolbar:has(.hidden-toggle)")
    toggle = tools.locator(".toolbar-actions .hidden-toggle")

    def card(job_id: str):
        return page.locator(f'#results-panel .results > [data-job-id="{job_id}"]')

    expect(toggle).to_be_hidden()
    for job_id in (kept, shown, failed):
        expect(card(job_id)).to_have_count(1)

    _pick(card(shown), ".hide-btn")
    expect(card(shown)).to_have_count(0)
    expect(toggle).to_have_text("Show hidden (1)")
    _pick(card(failed), ".hide-btn")
    expect(card(failed)).to_have_count(0)
    expect(toggle).to_have_text("Show hidden (2)")

    toggle.click()
    expect(toggle).to_have_text("Hide hidden")
    expect(toggle).to_have_attribute("aria-pressed", "true")
    expect(card(shown)).to_have_attribute("data-hidden", "")
    expect(card(failed)).to_have_attribute("data-hidden", "")
    expect(card(kept)).to_have_count(0)

    _pick(card(shown), ".unhide-btn")
    expect(card(shown)).to_have_count(0)
    expect(toggle).to_have_text("Hide hidden")

    toggle.click()
    expect(toggle).to_have_text("Show hidden (1)")
    expect(card(failed)).to_have_count(0)
    expect(card(shown)).to_have_count(1)
    expect(card(kept)).to_have_count(1)

    page.reload()
    expect(card(failed)).to_have_count(0)
    expect(card(shown)).to_have_count(1)
    expect(toggle).to_have_text("Show hidden (1)")

    toggle.click()
    expect(card(kept)).to_have_count(0)
    _pick(card(failed), ".unhide-btn")
    expect(toggle).to_be_hidden()
    for job_id in (kept, shown, failed):
        expect(card(job_id)).to_have_count(1)
    page.reload()
    expect(toggle).to_be_hidden()
    expect(card(failed)).to_have_count(1)
    assert va.js_errors == []


def test_show_hidden_right_after_hide_waits_for_the_save(va: Session, tmp_path: Path):
    job = _done("fake_ai", tmp_path / "a.png")
    va.goto("/generate")
    page = va.page
    card = page.locator(f'#results-panel .results > [data-job-id="{job}"]')
    toggle = page.locator(".ph-toolbar .hidden-toggle")

    held = []

    def hold(route):
        held.append(route)

    page.route("**/jobs/*/hidden", hold)
    _pick(card, ".hide-btn")
    expect(card).to_have_count(0)
    toggle.click()
    page.wait_for_timeout(300)
    expect(toggle).to_have_text("Show hidden (1)")
    assert len(held) == 1
    held[0].continue_()
    expect(card).to_have_attribute("data-hidden", "")
    expect(toggle).to_have_text("Hide hidden")
    page.unroute("**/jobs/*/hidden")
    assert va.js_errors == []
