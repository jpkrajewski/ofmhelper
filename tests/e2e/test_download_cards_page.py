"""/download-assets in a browser: every file of a download is its own card, on a
reload and when it finishes on the page, and Clone sends that card's video."""

from pathlib import Path

import pytest
from e2e_session import Session
from PIL import Image
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e

VA_ID = 2  # e2e conftest `_seed_users`
RESULT_TIMEOUT_MS = 30_000


def _download(folder: Path, *names: str) -> str:
    from ofmhelpers.db.stores.jobs import create_job
    from ofmhelpers.features.jobs.runner import run_job
    from ofmhelpers.integrations.downloaders.models import DownloadResult

    paths = []
    for name in names:
        (folder / name).write_bytes(name.encode())
        paths.append(folder / name)
    result = [DownloadResult(url="https://x/1", success=True, output_paths=paths)]
    job_id = create_job("download_media", {"urls": ["https://x/1"]}, actor=str(VA_ID))
    run_job(job_id, lambda r=result: r, {})
    return job_id


def _cards(va: Session, job_id: str):
    return va.page.locator(f'#results-panel .results > [data-job-id="{job_id}"]')


def test_each_file_is_a_card_and_clones_itself(va: Session, tmp_path: Path):
    job = _download(tmp_path, "first.mp4", "second.mp4", "third.mp4")

    va.goto("/download-assets")

    cards = _cards(va, job)
    expect(cards).to_have_count(3)
    cards.nth(1).locator(".send-to-clone").click()
    va.page.wait_for_url("**/clone?video=*")
    assert "second.mp4" in va.page.url
    assert "first.mp4" not in va.page.url
    assert "third.mp4" not in va.page.url


def test_hiding_a_card_hides_its_whole_download(va: Session, tmp_path: Path):
    job = _download(tmp_path, "first.mp4", "second.mp4")

    va.goto("/download-assets")

    cards = _cards(va, job)
    expect(cards).to_have_count(2)
    cards.first.locator(".menu summary").click()
    cards.first.locator(".hide-btn").click()
    expect(cards).to_have_count(0)


def _image(path: Path, colour: str) -> str:
    Image.new("RGB", (64, 64), colour).save(path, "PNG")
    return str(path)


def test_clean_images_adds_each_pick_and_cleans_them_all(va: Session, tmp_path: Path):
    """A second pick used to replace the first (a plain file input)."""
    first = _image(tmp_path / "first.png", "red")
    second = _image(tmp_path / "second.png", "green")
    third = _image(tmp_path / "third.png", "blue")
    va.goto("/download-assets")
    page = va.page
    runs: list[str] = []
    page.on(
        "request",
        lambda r: runs.append(r.url) if r.url.endswith("/clean-images/run") else None,
    )
    page.select_option("#tool-select", "clean_images")
    picks = page.locator('.file-picker[data-field="files"]')
    add = picks.locator(".file-add-input")
    listed = picks.locator(".file-order-list li")
    run = page.locator('#download-assets-form [type="submit"]')

    run.click()  # nothing picked: the form stops itself
    assert add.evaluate("e => e.validationMessage") == "Pick at least one image"
    assert runs == []

    add.set_input_files(first)
    add.set_input_files([second, third])
    expect(listed).to_have_count(3)
    listed.first.locator("button", has_text="✕").click()
    expect(listed).to_have_count(2)
    add.set_input_files(first)
    expect(listed).to_have_count(3)
    run.click()

    cards = page.locator('#results-panel .results > [data-task="clean_images"]')
    expect(cards.locator("img")).to_have_count(3, timeout=RESULT_TIMEOUT_MS)
    expect(listed).to_have_count(0)  # the next batch starts empty
    assert len(runs) == 1
    assert va.js_errors == []


def test_the_download_tool_runs_with_the_clean_picker_left_empty(va: Session):
    """The picker's stop applies only while its tool is the one chosen."""
    va.goto("/download-assets")
    page = va.page

    page.locator("#dl-urls").fill("https://www.example.com/watch/picker-empty")
    page.locator('#download-assets-form [type="submit"]').click()

    expect(page.locator("#results-panel .results > .result-item").first).to_be_visible(
        timeout=RESULT_TIMEOUT_MS
    )
    assert va.js_errors == []


def test_a_download_finishing_on_the_page_clones_its_video(va: Session):
    va.goto("/download-assets")
    page = va.page

    page.locator("#dl-urls").fill("https://www.example.com/watch/live-card")
    page.locator('#download-assets-form [type="submit"]').click()

    send = page.locator("#results-panel .results .send-to-clone").first
    expect(send).to_be_visible(timeout=RESULT_TIMEOUT_MS)
    send.click()
    page.wait_for_url("**/clone?video=*")
