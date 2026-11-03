"""/download-assets in a browser: every file of a download is its own card, on a
reload and when it finishes on the page, and Clone sends that card's video."""

from pathlib import Path

import pytest
from e2e_session import Session
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e

VA_ID = 2  # e2e conftest `_seed_users`
RESULT_TIMEOUT_MS = 30_000


def _download(folder: Path, *names: str) -> str:
    from ofmhelpers.integrations.downloaders.models import DownloadResult
    from ofmhelpers.web.stores.jobs import create_job, run_job

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


def test_a_download_finishing_on_the_page_clones_its_video(va: Session):
    va.goto("/download-assets")
    page = va.page

    page.locator("#dl-urls").fill("https://www.example.com/watch/live-card")
    page.locator('#download-assets-form [type="submit"]').click()

    send = page.locator("#results-panel .results .send-to-clone").first
    expect(send).to_be_visible(timeout=RESULT_TIMEOUT_MS)
    send.click()
    page.wait_for_url("**/clone?video=*")
