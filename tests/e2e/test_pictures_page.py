"""/pictures in a real browser: a preset made in its dialog, picked, a
picture added, Generate -- the card resolves on the page (kie.ai faked) --
and Recreate puts the run's picture back (static/js/pictures.js,
generation.js)."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import pytest
from playwright.sync_api import expect

from ofmhelpers.aigenproviders.kaiai.fake import FAKE_KEY

if TYPE_CHECKING:
    from pathlib import Path

    from e2e_session import Session

pytestmark = pytest.mark.e2e

RESULT_TIMEOUT_MS = 30_000


def test_a_preset_is_made_picked_and_run(va: Session, subject_images: list[Path]):
    va.goto("/pictures")
    expect(va.page.get_by_text("No presets yet")).to_be_visible()

    va.page.click("#preset-new")
    va.page.fill("#preset-name", "danewalker")
    va.page.fill(
        "#preset-prompt", "refer guy from first and second image, recreate third"
    )
    va.page.select_option("#preset-aspect", "3:4")
    va.page.set_input_files("#preset-images", [str(p) for p in subject_images])
    va.page.click('#preset-form button[type="submit"]')

    # Loaded, not just navigated: the picker listens from DOMContentLoaded.
    va.page.wait_for_url(re.compile(r"/pictures\?preset=\d+"))
    preset = va.page.locator(".pictures-preset:not([hidden])")
    expect(preset.locator("img")).to_have_count(2)
    expect(preset).to_contain_text("recreate third")
    expect(preset.locator(".badge")).to_have_text("3:4")
    # Edit opens with the preset's own values.
    preset.locator(".preset-edit").click()
    expect(va.page.locator("#preset-aspect")).to_have_value("3:4")
    va.page.locator("#preset-dialog [data-close]").click()

    picker = '.file-picker[data-field="pictures"] .file-add-input'
    va.page.set_input_files(picker, str(subject_images[0]))
    va.page.select_option("#provider-select", "kie")
    va.page.fill("#api-key", FAKE_KEY)
    with va.page.expect_response(lambda r: r.url.endswith("/pictures/run")) as run:
        va.page.click('#pictures-form button[type="submit"]')
    assert run.value.status == 200, run.value.text()
    [job_id] = run.value.json()["job_ids"]

    card = va.page.locator(f'.result-item[data-job-id="{job_id}"]')
    expect(card.locator(".download-btn")).to_be_visible(timeout=RESULT_TIMEOUT_MS)

    va.goto("/pictures")
    card = va.page.locator(f'.result-item[data-job-id="{job_id}"]')
    expect(card).to_contain_text("Pictures · danewalker")
    card.locator(".menu summary").click()
    card.locator(".recreate-btn").click()
    expect(
        va.page.locator('.file-picker[data-field="pictures"] .file-order-list li')
    ).to_have_count(1)
    assert va.js_errors == []
