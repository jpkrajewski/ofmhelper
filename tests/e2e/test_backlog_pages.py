lazy from pathlib import Path

import pytest
from e2e_session import Session
from playwright.sync_api import expect

from ofmhelpers.integrations.ai_providers.kie.fake import RESULT_HOST
lazy from ofmhelpers.db.clone_examples import store as clone_examples
lazy from ofmhelpers.db.devices import store as devices
lazy from ofmhelpers.db.devices.schema import DeviceFields
lazy from ofmhelpers.db.jobs.schema import JobPreview
lazy from ofmhelpers.db.jobs.store import create_job, set_job_preview
lazy from ofmhelpers.features.jobs.runner import run_job

pytestmark = pytest.mark.e2e

ADMIN_ID = 1
USER_ID = 2
PROMPT = "Replace the guy in @Video1 with subject from @Image1, no tattoos"


def _clone(owner: int, out: Path | None = None) -> str:

    job_id = create_job(
        "clone", {"prompt": PROMPT, "export": "capcut"}, actor=str(owner)
    )
    if out is not None:
        out.write_bytes(b"png")
        run_job(job_id, lambda: [{"name": out.name, "path": str(out)}], {})
    return job_id


def test_a_click_keeps_the_session(va: Session):
    va.goto("/clone")
    va.page.clock.install()
    va.page.clock.fast_forward(61_000)

    with va.page.expect_response("**/session") as kept:
        va.page.locator("h1").click()

    assert (kept.value.request.method, kept.value.status) == ("PUT", 204)
    assert "ofm_session=" in (kept.value.header_value("set-cookie") or "")


def test_a_generated_clone_still_being_exported_shows_one_video_and_says_so(
    va: Session,
):

    job_id = _clone(USER_ID)
    hosted = f"https://{RESULT_HOST}/results/e2e-stage.mp4"
    set_job_preview(job_id, JobPreview(remote_url=hosted, kind="video"))

    va.goto("/clone")
    card = va.page.locator(f'.result-item[data-job-id="{job_id}"]')

    expect(card.locator("video[data-preview]")).to_have_attribute("src", hosted)
    expect(card.locator("[data-stage]")).to_have_text(
        "Generated. Encoding: CapCut export…"
    )
    card.evaluate(
        """card => {
            const frame = document.createElement("img");
            frame.dataset.cloneSource = "";
            card.prepend(frame);
            card.dispatchEvent(new CustomEvent("gallery:card-preview", {bubbles: true}));
        }"""
    )
    expect(card.locator("[data-clone-source]")).to_have_count(0)
    assert va.js_errors == []


def test_the_sop_shows_the_fixed_examples_and_a_prompt_is_used(
    admin: Session, va: Session, tmp_path: Path
):

    job_id = _clone(ADMIN_ID, tmp_path / "result.png")
    clone_examples.add(job_id, "Remove tattoos", PROMPT)

    admin.goto("/clone")
    expect(admin.page.locator(".sop-add-btn")).to_have_count(0)

    va.goto("/clone")
    va.page.locator('#clone-form [name="prompt"]').fill("something else")
    va.page.click(".sop-open")
    shown = va.page.locator("#sop-dialog .sop-example")
    expect(shown.locator("h3")).to_have_text("Remove tattoos")
    expect(shown.locator(".sop-remove, .sop-rename")).to_have_count(0)
    shown.locator(".sop-use").click()

    expect(va.page.locator("#sop-dialog")).not_to_be_visible()
    expect(va.page.locator('#clone-form [name="prompt"]')).to_have_value(PROMPT)
    missing = "Failed to load resource"
    assert [e for e in admin.js_errors + va.js_errors if missing not in e] == []


def test_history_brings_a_deleted_phone_back(va: Session):

    phone = devices.add_device(DeviceFields(label="e2e kept"), USER_ID)
    devices.delete_device(phone, USER_ID)

    va.goto("/phones")
    expect(va.page.locator(".ph-card", has_text="e2e kept")).to_have_count(0)
    va.page.click("#ph-history-open")
    lines = va.page.locator("#ph-revisions .ph-revision")
    expect(lines).to_have_count(2)
    expect(lines.first).to_contain_text("Now")
    lines.nth(1).locator(".ph-revision-restore").click()
    expect(va.page.locator("#ph-restore-question")).to_contain_text("Go back to")
    with va.page.expect_navigation():
        va.page.click('#ph-restore-confirm button[value="confirm"]')

    expect(va.page.locator(".ph-card", has_text="e2e kept")).to_have_count(1)
    assert va.js_errors == []
