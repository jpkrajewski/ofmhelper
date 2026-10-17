"""End to end, in a real browser against a running stack (see conftest.py):
log in, open every page, make a fake asset and download it, and run a clone
batch with a wrong kie.ai key so nothing is spent."""

import re
from pathlib import Path

import pytest
from e2e_session import Session
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e

VA_PAGES = ["/generate", "/clone", "/reels", "/download-assets", "/uniqify"]
ADMIN_ONLY_PAGES = ["/character-sheet", "/applications", "/file-manager", "/action-log"]

# A generation is polled with backoff; a fake one takes ~1 s plus the poll.
RESULT_TIMEOUT_MS = 30_000
# The live gallery polls every OFM_GALLERY_LIVE_POLL_S (5 s by default).
LIVE_TIMEOUT_MS = 15_000


def _nav_paths(session: Session) -> list[str]:
    """The header's page links, read off an app page (login lands on the
    public landing page, which has no app header)."""
    session.goto("/generate")
    links = session.page.locator(".site-header nav a")
    return [links.nth(i).get_attribute("href") for i in range(links.count())]


def test_admin_opens_every_page(admin: Session):
    assert _nav_paths(admin) == VA_PAGES + ADMIN_ONLY_PAGES

    for path in VA_PAGES + ADMIN_ONLY_PAGES:
        assert admin.goto(path) == 200, path
        admin.page.wait_for_load_state("networkidle")

    assert admin.js_errors == []


def test_va_sees_only_its_pages_and_none_of_the_admin_ones(va: Session):
    assert _nav_paths(va) == VA_PAGES

    for path in VA_PAGES:
        assert va.goto(path) == 200, path
        va.page.wait_for_load_state("networkidle")
    # Checked before the admin pages: the browser logs each 403 below as a
    # console error of its own.
    assert va.js_errors == []

    for path in ADMIN_ONLY_PAGES:
        assert va.goto(path) == 403, path


def test_fake_asset_is_generated_shown_live_and_downloads_as_img(
    va: Session, coworker: Session
):
    coworker.goto("/generate")
    va.goto("/generate")
    va.page.select_option("#tool-select", "fake_ai")
    va.page.fill("#kie-api-key", "unused-by-fake-ai")
    va.page.fill('textarea[name="prompt"]', "e2e: a red studio backdrop")

    with va.page.expect_response(lambda r: r.url.endswith("/fake-ai/run")) as run:
        va.page.click('#generate-form button[type="submit"]')
    job_id = run.value.json()["job_id"]

    card = f'.result-item[data-job-id="{job_id}"]'
    download = va.page.locator(f"{card} a.download-btn")
    expect(download).to_be_visible(timeout=RESULT_TIMEOUT_MS)
    expect(va.page.locator(card)).to_have_count(1)  # no duplicate card
    # The coworker's open tab gets it without a reload.
    expect(coworker.page.locator(card)).to_have_count(1, timeout=LIVE_TIMEOUT_MS)

    with va.page.expect_download() as saved:
        download.click()
    assert re.fullmatch(r"IMG_\d{4}\.png", saved.value.suggested_filename)

    assert va.js_errors == []
    assert coworker.js_errors == []


def test_picked_files_survive_a_model_switch(va: Session, subject_images: list[Path]):
    va.goto("/generate")
    va.page.select_option("#tool-select", "seedance")
    images = 'fieldset[data-tool="{}"] .file-picker[data-kind="image"]'
    for path in subject_images:  # a one-at-a-time picker, like a person
        va.page.locator(f"{images.format('seedance')} .file-add-input").set_input_files(
            str(path)
        )

    va.page.select_option("#tool-select", "nanobanana")

    expect(va.page.locator(f"{images.format('nanobanana')} li")).to_have_count(2)
    expect(va.page.locator(f"{images.format('seedance')} li")).to_have_count(0)


def test_clone_with_a_wrong_key_queues_the_batch_and_fails_cleanly(
    va: Session,
    coworker: Session,
    subject_images: list[Path],
    source_videos: list[Path],
):
    coworker.goto("/clone")
    va.goto("/clone")
    va.page.fill("#kie-api-key", "e2e-wrong-key")
    va.page.locator('.file-picker[data-field="image"] .file-add-input').set_input_files(
        [str(p) for p in subject_images]
    )
    va.page.locator(
        '.file-picker[data-field="videos"] .file-add-input'
    ).set_input_files([str(p) for p in source_videos])

    with va.page.expect_response(lambda r: r.url.endswith("/clone/run")) as run:
        va.page.click('#clone-form button[type="submit"]')
    body = run.value.json()
    assert len(body["job_ids"]) == len(source_videos)
    batch_id = re.search(r'data-batch="([^"]+)"', body["html"]).group(1)

    batch = f'#clone-batches > .clone-batch[data-batch="{batch_id}"]'
    title = va.page.locator(f"{batch} .clone-batch-title")
    expect(title).to_contain_text("2 videos")
    expect(title).to_contain_text("2 images")
    expect(va.page.locator(batch)).to_have_count(1)  # no duplicate batch
    expect(coworker.page.locator(batch)).to_have_count(1, timeout=LIVE_TIMEOUT_MS)

    # kie.ai rejects the key at the first upload: every run fails with it,
    # and nothing is generated or paid for.
    failed = va.page.locator(f"{batch} .result-file", has_text="Wrong API Key")
    expect(failed).to_have_count(len(source_videos), timeout=RESULT_TIMEOUT_MS)

    # Recreate brings the whole batch back without re-uploading.
    va.page.locator(f"{batch} .batch-recreate-btn").click()
    expect(va.page.locator('.file-picker[data-field="image"] li')).to_have_count(2)
    expect(va.page.locator('.file-picker[data-field="videos"] li')).to_have_count(2)

    assert va.js_errors == []
    assert coworker.js_errors == []


# Live Instagram: public profiles that each have at least one reel past 5x
# their followers and at most 15 s long.
VIRAL_PROFILES = ["sheep_love33", "https://www.instagram.com/brookslockvip/"]
SCAN_TIMEOUT_MS = 120_000


def test_viral_reels_open_on_clone_and_run_with_a_wrong_key(
    va: Session, subject_images: list[Path]
):
    va.goto("/reels")
    va.page.fill("#reels-profiles", "\n".join(VIRAL_PROFILES))
    va.page.click('#reels-form button[type="submit"]')

    status = va.page.locator("#reels-form .generation-status")
    expect(status).to_contain_text("Added", timeout=SCAN_TIMEOUT_MS)
    # A first scan runs on the worker; the page reloads itself once it is done.
    expect(va.page.locator(".reels-watch-item[data-pending]")).to_have_count(
        0, timeout=SCAN_TIMEOUT_MS
    )
    for username in ("sheep_love33", "brookslockvip"):
        expect(
            va.page.locator(f'.reels-watch-item[data-username="{username}"]')
        ).to_have_count(1)
    cards = va.page.locator(".reel-card[data-video-url]")
    assert cards.count() >= len(VIRAL_PROFILES)
    # One feed, most viewed first, across profiles.
    views = va.page.locator(".reel-card").evaluate_all(
        "cs => cs.map(c => Number(c.dataset.views))"
    )
    assert views == sorted(views, reverse=True)
    # Only lazy thumbnails until a hover.
    expect(va.page.locator("#reels-feed video")).to_have_count(0)

    # Hovering creates the video and plays it straight from Instagram's CDN.
    first = cards.first
    first.hover()
    va.page.wait_for_function(
        "v => v.currentTime > 0 && !v.paused",
        arg=first.locator("video").element_handle(timeout=SCAN_TIMEOUT_MS),
        timeout=SCAN_TIMEOUT_MS,
    )

    # A click anywhere on a card picks it.
    picked = [cards.nth(i) for i in range(len(VIRAL_PROFILES))]
    for card in picked:
        card.click()
    expect(picked[0]).to_have_attribute("aria-pressed", "true")
    clone_btn = va.page.locator("#reels-clone")
    expect(clone_btn).to_have_text(f"Clone selected ({len(picked)})")

    # The list and the picks survive a reload.
    va.page.reload()
    for username in ("sheep_love33", "brookslockvip"):
        expect(
            va.page.locator(f'.reels-watch-item[data-username="{username}"]')
        ).to_have_count(1)
    expect(clone_btn).to_have_text(f"Clone selected ({len(picked)})")

    with va.page.expect_navigation(
        url=re.compile(r"/clone\?video="), timeout=SCAN_TIMEOUT_MS
    ):
        va.page.click("#reels-clone")

    videos = va.page.locator('.file-picker[data-field="videos"] li')
    expect(videos).to_have_count(len(picked))
    va.page.fill("#kie-api-key", "e2e-wrong-key")
    va.page.locator('.file-picker[data-field="image"] .file-add-input').set_input_files(
        [str(p) for p in subject_images]
    )
    with va.page.expect_response(lambda r: r.url.endswith("/clone/run")) as run:
        va.page.click('#clone-form button[type="submit"]')
    body = run.value.json()
    assert len(body["job_ids"]) == len(picked)
    batch_id = re.search(r'data-batch="([^"]+)"', body["html"]).group(1)

    batch = f'#clone-batches > .clone-batch[data-batch="{batch_id}"]'
    failed = va.page.locator(f"{batch} .result-file", has_text="Wrong API Key")
    expect(failed).to_have_count(len(picked), timeout=RESULT_TIMEOUT_MS)

    assert va.js_errors == []
