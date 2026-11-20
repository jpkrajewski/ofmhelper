import json
import re
import subprocess
import zipfile
from pathlib import Path

import pytest
from app_pages import ADMIN_ONLY_PAGES, VA_PAGES
from download_files import removed_download
from e2e_session import Session, connect_google, my_root
from playwright.sync_api import expect

from ofmhelpers.db.devices import store as devices
from ofmhelpers.db.devices.schema import DeviceFields, LoginFields
from ofmhelpers.db.jobs.store import create_job, fail_job
from ofmhelpers.db.watched_profiles import store as watched_profiles
from ofmhelpers.integrations.gdrive.fake import FakeDrive
lazy from ofmhelpers.features.jobs.runner import run_job

pytestmark = pytest.mark.e2e

RESULT_TIMEOUT_MS = 30_000
LIVE_TIMEOUT_MS = 15_000
VA_ID = 2


def _nav_paths(session: Session) -> list[str]:
    session.goto("/generate")
    links = session.page.locator(".site-header nav a")
    return [links.nth(i).get_attribute("href") for i in range(links.count())]


def test_the_header_switches_to_dark_and_back_and_it_sticks(va: Session):
    va.goto("/generate")
    html = va.page.locator("html")
    expect(html).not_to_have_attribute("data-theme", "dark")
    va.page.click(".theme-toggle")
    expect(html).to_have_attribute("data-theme", "dark")
    va.goto("/clone")
    expect(va.page.locator("html")).to_have_attribute("data-theme", "dark")
    va.page.click(".theme-toggle")
    va.goto("/generate")
    expect(va.page.locator("html")).not_to_have_attribute("data-theme", "dark")
    assert va.js_errors == []


def test_va_sees_only_its_pages_and_none_of_the_admin_ones(va: Session):
    assert _nav_paths(va) == VA_PAGES

    for path in VA_PAGES:
        assert va.goto(path) == 200, path
        expect(va.page.locator(".site-header nav")).to_be_visible()
    for path in ADMIN_ONLY_PAGES:
        assert va.goto(path) == 200, path
        assert va.page.url.endswith("/home"), path
    assert va.js_errors == []


def test_an_admin_adds_a_user_who_signs_in_with_google_and_with_a_password(
    admin: Session, browser, base_url
):
    admin.goto("/users")
    admin.page.fill("#users-email", "newbie@example.com")
    admin.page.click(".users-add button[type=submit]")
    link = admin.page.input_value("#users-link")
    assert "/password/" in link

    newbie = browser.new_context(base_url=base_url)
    page = newbie.new_page()
    page.goto("/login")
    page.click('.login-provider[data-provider="google"]')
    page.fill("#fake-email", "newbie@example.com")
    with page.expect_navigation():
        page.click("button[type=submit]")
    expect(page.locator("#fake-email")).to_be_visible()
    page.fill("#fake-email", "newbie@example.com")
    with page.expect_navigation():
        page.click("button[type=submit]")
    assert "/auth/" not in page.url
    page.goto("/account")
    assert page.url.endswith("/account")
    expect(page.locator('[data-provider="google"]')).to_contain_text(
        "newbie@example.com"
    )
    expect(page.locator(".card").nth(1)).to_contain_text("Connected")
    page.click(".logout-form button")

    page.goto(link)
    page.fill("#password", "a long new password")
    page.fill("#confirm", "a long new password")
    with page.expect_navigation():
        page.click("button[type=submit]")
    page.goto("/account")
    expect(page.locator(".account-list")).to_contain_text("Password · set")
    page.click(".logout-form button")
    page.goto("/login")
    page.fill("#email", "newbie@example.com")
    page.fill("#password", "a long new password")
    with page.expect_navigation():
        page.click("button[type=submit]")
    assert "/login" not in page.url
    newbie.close()
    assert admin.js_errors == []


def test_fake_asset_is_generated_shown_live_and_downloads_as_img(
    va: Session, coworker: Session
):
    coworker.goto("/generate")
    va.goto("/generate")
    va.page.select_option("#provider-select", "fake")
    va.page.select_option("#model-select", "fake:image")
    expect(va.page.locator("#api-key-field")).to_be_hidden()
    expect(va.page.locator('#model-fields [name="outcome"]')).to_be_visible()
    va.page.fill('#model-fields [name="delay"]', "0")
    va.page.fill('textarea[name="prompt"]', "e2e: a red studio backdrop")

    with va.page.expect_response(lambda r: r.url.endswith("/ai/run")) as run:
        va.page.click('#generate-form button[type="submit"]')
    job_id = run.value.json()["job_id"]

    card = f'.result-item[data-job-id="{job_id}"]'
    download = va.page.locator(f"{card} a.download-btn")
    expect(download).to_be_visible(timeout=RESULT_TIMEOUT_MS)
    expect(va.page.locator(card)).to_have_count(1)
    expect(coworker.page.locator(card)).to_have_count(1, timeout=LIVE_TIMEOUT_MS)

    with va.page.expect_download() as saved:
        download.click()
    assert re.fullmatch(r"IMG_\d{4}\.png", saved.value.suggested_filename)

    assert va.js_errors == []
    assert coworker.js_errors == []


def test_recreate_fills_the_jobs_prompt_at_once_and_survives_click_spam(va: Session):
    prompt = "e2e: recreate me exactly"
    va.goto("/generate")
    va.page.select_option("#provider-select", "fake")
    va.page.select_option("#model-select", "fake:image")
    va.page.fill('#model-fields [name="delay"]', "0")
    va.page.fill('textarea[name="prompt"]', prompt)
    with va.page.expect_response(lambda r: r.url.endswith("/ai/run")) as run:
        va.page.click('#generate-form button[type="submit"]')
    card = f'.result-item[data-job-id="{run.value.json()["job_id"]}"]'
    expect(va.page.locator(f"{card} .download-btn")).to_be_visible(
        timeout=RESULT_TIMEOUT_MS
    )

    fields_asked: list[str] = []

    def slow_fields(route):
        fields_asked.append(route.request.url)
        va.page.wait_for_timeout(800)
        route.continue_()

    va.page.route("**/ai/fields?*", slow_fields)
    va.goto("/generate")
    _swap_fields(va, lambda: va.page.select_option("#provider-select", "kie"))

    def recreate() -> None:
        va.page.locator(f"{card} .menu summary").click()
        va.page.locator(f"{card} .recreate-btn").click()

    recreate()
    assert va.page.input_value('textarea[name="prompt"]') == prompt

    for _ in range(25):
        recreate()
    expect(va.page.locator('#model-fields [name="delay"]')).to_be_visible()
    expect(va.page.locator('textarea[name="prompt"]')).to_have_value(prompt)
    assert va.page.input_value("#model-select") == "fake:image"
    assert [u for u in fields_asked if "fake%3Aimage" in u] == fields_asked[:1]
    assert va.js_errors == []


def _swap_fields(session: Session, pick) -> None:
    shown = session.page.locator("#model-fields").element_handle()
    pick()
    session.page.wait_for_function("el => !el.isConnected", arg=shown)


def test_picked_files_survive_a_model_switch(va: Session, subject_images: list[Path]):
    va.goto("/generate")
    _swap_fields(va, lambda: va.page.select_option("#provider-select", "kie"))
    _swap_fields(
        va, lambda: va.page.select_option("#model-select", "kie:bytedance/seedance-2-5")
    )
    seedance = '#model-fields .file-picker[data-field="reference_image_urls"]'
    for path in subject_images:
        va.page.locator(f"{seedance} .file-add-input").set_input_files(str(path))

    va.page.select_option("#model-select", "kie:nano-banana-pro")
    nbp = '#model-fields .file-picker[data-field="image_input"]'
    expect(va.page.locator(f"{nbp} li")).to_have_count(2)
    expect(va.page.locator(seedance)).to_have_count(0)

    va.page.select_option("#provider-select", "higgsfield")
    va.page.select_option(
        "#model-select", "higgsfield:bytedance/seedance-2.5/reference-to-video"
    )
    hf = '#model-fields .file-picker[data-field="image_urls"]'
    expect(va.page.locator(f"{hf} li")).to_have_count(2)
    assert va.js_errors == []


def test_clone_with_a_wrong_key_queues_a_card_per_video_and_fails_cleanly(
    va: Session,
    coworker: Session,
    subject_images: list[Path],
    source_videos: list[Path],
):
    coworker.goto("/clone")
    va.goto("/clone")
    va.page.select_option("#provider-select", "kie")
    va.page.fill("#api-key", "e2e-wrong-key")
    va.page.locator('.file-picker[data-field="image"] .file-add-input').set_input_files(
        [str(p) for p in subject_images]
    )
    va.page.locator(
        '.file-picker[data-field="videos"] .file-add-input'
    ).set_input_files([str(p) for p in source_videos])

    with va.page.expect_response(lambda r: r.url.endswith("/clone/run")) as run:
        va.page.click('#clone-form button[type="submit"]')
        va.page.click("#clone-confirm-go")
    body = run.value.json()
    assert len(body["job_ids"]) == len(source_videos)
    cards = ", ".join(f'#clone-gallery > [data-job-id="{j}"]' for j in body["job_ids"])
    expect(va.page.locator(cards)).to_have_count(len(source_videos))
    expect(coworker.page.locator(cards)).to_have_count(
        len(source_videos), timeout=LIVE_TIMEOUT_MS
    )

    failed = va.page.locator("#clone-gallery .result-failed").filter(
        has_text="kie.ai: Wrong API key"
    )
    expect(failed).to_have_count(len(source_videos), timeout=RESULT_TIMEOUT_MS)
    expect(failed.first).to_have_attribute("data-reason", "Wrong API key")

    for page in (va.page, coworker.page):
        pictures = page.locator(f":is({cards}) > img[data-clone-source]:first-child")
        expect(pictures).to_have_count(len(source_videos), timeout=LIVE_TIMEOUT_MS)

    first = va.page.locator(f'#clone-gallery > [data-job-id="{body["job_ids"][0]}"]')
    expect(
        first.locator(".result-actions .menu .menu-item:not([hidden])")
    ).to_have_text(["Hide"])
    first.locator(".recreate-btn").click()
    expect(va.page.locator('.file-picker[data-field="image"] li')).to_have_count(2)
    expect(va.page.locator('.file-picker[data-field="videos"] li')).to_have_count(1)
    va.page.wait_for_load_state("networkidle")
    expect(va.page.locator(".file-picker-missing")).to_have_count(0)
    expect(va.page.locator('.file-picker[data-field="videos"] li')).to_have_count(1)

    assert va.js_errors == []
    assert coworker.js_errors == []

    expect(va.page.locator("#clone-cost-summary")).to_have_text(
        re.compile(r"^1 run · [\d.]+ s of video$")
    )
    params = json.loads(first.get_attribute("data-params") or "{}")
    Path(params["reference_videos"][0]).unlink()
    first.locator(".recreate-btn").click()
    videos = va.page.locator('.file-picker[data-field="videos"]')
    expect(videos.locator(".file-picker-missing")).to_contain_text(
        "is not on this server: pick it again"
    )
    expect(videos.locator("li")).to_have_count(0)
    expect(va.page.locator('.file-picker[data-field="image"] li')).to_have_count(2)
    assert all("404" in e for e in va.js_errors), va.js_errors


def test_recreate_downloads_a_clones_removed_video_again(
    va: Session, tmp_path: Path, app_dir: Path
):
    removed = removed_download(
        tmp_path, app_dir / "uploads", VA_ID, "https://www.example.com/watch/recreate"
    )
    params = {
        "provider": "kie",
        "prompt": "e2e: clone it again",
        "reference_images": [],
        "reference_videos": [removed.asset_path],
        "resolution": "720p",
    }
    clone = create_job("clone", params, actor=str(VA_ID))
    fail_job(clone, "kie.ai: no credits left")

    va.goto("/clone")
    va.page.locator(f'#clone-gallery > [data-job-id="{clone}"] .recreate-btn').click()

    videos = va.page.locator('.file-picker[data-field="videos"]')
    expect(videos.locator("li")).to_have_count(1)
    expect(videos.locator(".reused-badge")).to_have_text("Downloading again…")
    expect(videos.locator(".reused-badge")).to_have_text(
        "reused", timeout=RESULT_TIMEOUT_MS
    )
    expect(videos.locator("li")).to_have_count(1)
    expect(videos.locator(".file-picker-missing")).to_have_count(0)
    expect(videos.locator("li img.thumb-small")).to_have_count(1)
    assert va.js_errors == []


REELS_PARKED = pytest.mark.skip(reason="/reels is retired: auth/access.py RETIRED")

VIRAL_PROFILES = ["sheep_love33", "https://www.instagram.com/brookslockvip/"]
FEED_REELS = 9
SCAN_TIMEOUT_MS = 30_000


def test_clone_output_selects_and_switches_line_up(va: Session):
    va.goto("/clone")
    row = va.page.locator(".clone-output-row")
    va.page.locator("#clone-export").focus()

    selects = [row.locator("select").nth(i).bounding_box() for i in range(2)]
    switches = [row.locator(".check-field").nth(i).bounding_box() for i in range(2)]

    assert selects[0]["width"] == selects[1]["width"]
    assert switches[0]["x"] == switches[1]["x"]
    assert va.page.eval_on_selector(
        "#clone-export", "e => getComputedStyle(e).backgroundImage"
    ).startswith("linear-gradient")
    assert va.js_errors == []


def test_clone_long_prices_a_long_16_9_video_before_it_runs(
    va: Session, tmp_path: Path
):
    video = tmp_path / "long.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=128x72:rate=10",
            "-t",
            "32",
            "-pix_fmt",
            "yuv420p",
            str(video),
        ],
        check=True,
        timeout=60,
    )
    va.goto("/clone-long")
    va.page.select_option("#provider-select", "kie")
    quote = va.page.locator("#clone-long-quote")
    expect(quote).to_be_hidden()

    va.page.locator('.file-picker[data-field="video"] .file-add-input').set_input_files(
        str(video)
    )

    expect(quote).to_have_text("32 s · 3 pieces · $5.12 · 1,024 credits")
    va.page.select_option("#clone-long-resolution", "1080P")
    expect(quote).to_contain_text("3 pieces")
    assert va.js_errors == []


def test_clone_long_start_queues_a_card_that_fails_cleanly_on_a_wrong_key(
    va: Session, subject_images: list[Path], source_videos: list[Path]
):
    va.goto("/clone-long")
    expect(va.page.locator("#clone-long-empty")).to_be_visible()
    va.page.select_option("#provider-select", "kie")
    va.page.fill("#api-key", "e2e-wrong-key")
    va.page.locator('.file-picker[data-field="image"] .file-add-input').set_input_files(
        str(subject_images[0])
    )
    va.page.locator('.file-picker[data-field="video"] .file-add-input').set_input_files(
        str(source_videos[0])
    )

    with va.page.expect_response(lambda r: r.url.endswith("/clone-long/run")) as run:
        va.page.click('#clone-long-form button[type="submit"]')

    job_id = run.value.json()["job_id"]
    card = va.page.locator(f'#clone-long-gallery > [data-job-id="{job_id}"]')
    expect(card).to_have_count(1)
    expect(va.page.locator("#clone-long-empty")).to_be_hidden()
    expect(card.locator(".result-failed")).to_have_text(
        re.compile("Wrong API key"), timeout=RESULT_TIMEOUT_MS
    )

    va.page.reload()
    va.page.fill("#clone-long-prompt", "")
    va.page.locator(
        f'#clone-long-gallery > [data-job-id="{job_id}"] .recreate-btn'
    ).click()
    for field in ("image", "video"):
        picked = va.page.locator(f'.file-picker[data-field="{field}"] li')
        expect(picked).to_have_count(1)
        expect(picked.locator(".reused-badge")).to_have_count(1)
    expect(va.page.locator("#clone-long-prompt")).not_to_have_value("")
    expect(va.page.locator(".file-picker-missing")).to_have_count(0)
    assert va.js_errors == []


def test_clone_prices_the_picked_videos_live(va: Session, source_videos: list[Path]):
    va.goto("/clone")
    va.page.select_option("#provider-select", "kie")
    panel = va.page.locator("#clone-cost")
    expect(panel).to_be_hidden()
    va.page.locator(
        '.file-picker[data-field="videos"] .file-add-input'
    ).set_input_files([str(p) for p in source_videos])

    expect(panel).to_be_visible()
    expect(va.page.locator("#clone-cost-lines .cost-line")).to_have_count(2)
    expect(va.page.locator("#clone-cost-summary")).to_have_text(
        "2 runs · 8.0 s of video"
    )
    expect(va.page.locator("#clone-cost-usd")).to_have_text("$1.28")
    expect(va.page.locator("#clone-cost-credits")).to_have_text("256 credits")
    expect(va.page.locator("#clone-cost-rate")).to_have_text(
        "720P · $0.16 per video second"
    )

    va.page.select_option("#clone-resolution", "1080P")
    expect(va.page.locator("#clone-cost-usd")).to_have_text("$2.56")

    va.page.select_option("#provider-select", "higgsfield")
    expect(va.page.locator("#clone-cost-usd")).to_have_text("$6.93")
    va.page.select_option("#clone-resolution", "720P")
    expect(va.page.locator("#clone-cost-usd")).to_have_text("$3.47")
    expect(va.page.locator("#clone-cost-credits")).to_have_text("55.4 credits")
    expect(va.page.locator("#clone-cost-rate")).to_have_text(
        "720P · $0.433 per video second"
    )
    expect(va.page.locator("#clone-cost-note")).to_contain_text("Measured on your runs")
    va.page.select_option("#provider-select", "kie")
    va.page.select_option("#clone-resolution", "1080P")
    expect(va.page.locator("#clone-cost-usd")).to_have_text("$2.56")

    va.page.locator(
        '.file-picker[data-field="videos"] .file-order-list button', has_text="✕"
    ).first.click()
    expect(va.page.locator("#clone-cost-lines .cost-line")).to_have_count(1)
    expect(va.page.locator("#clone-cost-usd")).to_have_text("$1.60")


@REELS_PARKED
def test_viral_reels_open_on_clone_and_run_with_a_wrong_key(
    va: Session, subject_images: list[Path]
):
    va.goto("/reels")
    va.page.fill("#reels-profiles", "\n".join(VIRAL_PROFILES))
    va.page.click('#reels-form button[type="submit"]')

    status = va.page.locator("#reels-form .generation-status")
    expect(status).to_contain_text("Added", timeout=SCAN_TIMEOUT_MS)
    cards = va.page.locator(".reel-card[data-video-url]")
    expect(cards).to_have_count(FEED_REELS, timeout=SCAN_TIMEOUT_MS)
    expect(va.page.locator(".reels-watch-item[data-pending]")).to_have_count(0)
    for username in ("sheep_love33", "brookslockvip"):
        expect(
            va.page.locator(f'.reels-watch-item[data-username="{username}"]')
        ).to_have_count(1)
    views = va.page.locator(".reel-card").evaluate_all(
        "cs => cs.map(c => Number(c.dataset.views))"
    )
    assert views == sorted(views, reverse=True)
    expect(va.page.locator("#reels-feed video")).to_have_count(0)

    first = cards.first
    first.hover()
    va.page.wait_for_function(
        "v => v.currentTime > 0 && !v.paused",
        arg=first.locator("video").element_handle(timeout=SCAN_TIMEOUT_MS),
        timeout=SCAN_TIMEOUT_MS,
    )

    picked = [cards.nth(i) for i in range(len(VIRAL_PROFILES))]
    for card in picked:
        card.click()
    expect(picked[0]).to_have_attribute("aria-pressed", "true")
    picked_count = va.page.locator("#reels-picked-count")
    expect(picked_count).to_have_text(f"{len(picked)} reels selected")
    expect(va.page.locator("#reels-clone")).to_be_enabled()

    va.page.reload()
    for username in ("sheep_love33", "brookslockvip"):
        expect(
            va.page.locator(f'.reels-watch-item[data-username="{username}"]')
        ).to_have_count(1)
    expect(picked_count).to_have_text(f"{len(picked)} reels selected")

    with va.page.expect_navigation(
        url=re.compile(r"/clone\?video="), timeout=SCAN_TIMEOUT_MS
    ):
        va.page.click("#reels-clone")

    videos = va.page.locator('.file-picker[data-field="videos"] li')
    expect(videos).to_have_count(len(picked))
    va.page.fill("#api-key", "e2e-wrong-key")
    va.page.locator('.file-picker[data-field="image"] .file-add-input').set_input_files(
        [str(p) for p in subject_images]
    )
    with va.page.expect_response(lambda r: r.url.endswith("/clone/run")) as run:
        va.page.click('#clone-form button[type="submit"]')
        va.page.click("#clone-confirm-go")
    body = run.value.json()
    assert len(body["job_ids"]) == len(picked)
    failed = va.page.locator(
        "#clone-gallery .result-failed", has_text="Higgsfield: Wrong API key"
    )
    expect(failed).to_have_count(len(picked), timeout=RESULT_TIMEOUT_MS)

    assert va.js_errors == []


def _add_viral_profiles(session: Session) -> None:
    session.goto("/reels")
    session.page.fill("#reels-profiles", "\n".join(VIRAL_PROFILES))
    session.page.click('#reels-form button[type="submit"]')
    expect(session.page.locator("#reels-form .generation-status")).to_contain_text(
        "Added", timeout=SCAN_TIMEOUT_MS
    )
    expect(session.page.locator(".reel-card[data-video-url]")).to_have_count(
        FEED_REELS, timeout=SCAN_TIMEOUT_MS
    )


CHIP_COUNTS = {
    "reels-filter-group": {"All": FEED_REELS, "vip": 4},
    "reels-filter-rank": {
        "All": FEED_REELS,
        "legendary": 2,
        "epic": 2,
        "rare": 2,
        "uncommon": 1,
        "common": 2,
    },
    "reels-filter-reach": {"Any": FEED_REELS, "2": 7, "3": 7, "5": 6, "10": 5},
}


def _chip_key(label: str, href: str) -> str:
    query = (
        dict(re.findall(r"(\w+)=([^&]+)", href.split("?", 1)[-1]))
        if "?" in href
        else {}
    )
    return query.get("group") or query.get("rank") or query.get("ratio") or label


@REELS_PARKED
def test_every_reels_filter_chip_shows_exactly_its_reels(va: Session):
    _add_viral_profiles(va)
    watched_profiles.set_group("brookslockvip", "vip", user_id=2)
    for nav, expected in CHIP_COUNTS.items():
        va.goto("/reels")
        chips = va.page.locator(f'nav[aria-labelledby="{nav}"] a')
        keys = [
            _chip_key(
                chips.nth(i).inner_text().split()[0], chips.nth(i).get_attribute("href")
            )
            for i in range(chips.count())
        ]
        assert keys == list(expected), nav
        for i, key in enumerate(keys):
            va.goto("/reels")
            with va.page.expect_navigation():
                chips.nth(i).click()
            expect(chips.nth(i)).to_have_attribute("aria-current", "page")
            expect(va.page.locator(".reel-card")).to_have_count(expected[key])
    assert va.js_errors == []


@REELS_PARKED
def test_three_picked_reels_download_as_one_zip(va: Session, tmp_path: Path):
    _add_viral_profiles(va)
    va.goto("/reels")
    cards = va.page.locator(".reel-card[data-video-url]")
    expect(cards).to_have_count(FEED_REELS)
    codes = [cards.nth(i).get_attribute("data-code") for i in range(3)]
    for i in range(3):
        cards.nth(i).click()
    expect(va.page.locator("#reels-picked-count")).to_have_text("3 reels selected")

    with va.page.expect_download(timeout=SCAN_TIMEOUT_MS) as download:
        va.page.click("#reels-download")
    archive = tmp_path / download.value.suggested_filename
    download.value.save_as(archive)
    with zipfile.ZipFile(archive) as zf:
        assert sorted(zf.namelist()) == sorted(f"{c}.mp4" for c in codes)
        assert all(info.file_size > 0 for info in zf.infolist())
    expect(va.page.locator("#reels-clone-status")).to_contain_text("Downloaded 3 reels")

    archive.unlink()
    download.value.delete()
    assert not archive.exists()
    for i in range(3):
        cards.nth(i).click()
    expect(va.page.locator("#reels-picked-count")).to_have_text("No reels selected")


TRACKED_ACCOUNT = "yuki_kanasiva"


def test_tracked_account_is_scanned_annotated_and_removed(va: Session):
    va.goto("/accounts")
    va.page.click("#accounts-add-open")
    va.page.fill("#accounts-add-handles", f"@{TRACKED_ACCOUNT}")
    va.page.fill("#accounts-add-creator", "e2e creator")
    va.page.click('#accounts-add-form button[type="submit"]')

    row = va.page.locator("#accounts-table tbody tr", has_text=f"@{TRACKED_ACCOUNT}")
    expect(row).to_have_count(1, timeout=SCAN_TIMEOUT_MS)
    expect(row).to_have_attribute(
        "data-status", re.compile("active|private"), timeout=SCAN_TIMEOUT_MS
    )
    expect(row.locator(".accounts-creator-tag")).to_have_text("e2e creator")
    expect(row.locator('td[data-label="Followers"]')).to_contain_text("850")

    expect(row.locator('td[data-label="Phone"]')).to_be_visible()

    va.page.fill("#accounts-search", TRACKED_ACCOUNT[:6])
    expect(row).to_be_visible()
    va.page.fill("#accounts-search", "zzzz-no-such")
    expect(row).to_be_hidden()
    va.page.fill("#accounts-search", "")

    va.page.select_option('select[name="creator"]', "e2e creator")
    expect(va.page.locator("#accounts-table tbody tr")).to_have_count(1)

    with va.page.expect_navigation():
        row.locator(".accounts-go a").click()
    expect(va.page.locator(".accounts-identity")).to_contain_text(f"@{TRACKED_ACCOUNT}")
    kpis = va.page.locator(".stat")
    expect(kpis).to_have_count(4)
    expect(kpis.first).to_contain_text("Followers")
    with va.page.expect_navigation():
        va.page.select_option(".accounts-period select", "30")
    expect(kpis.nth(1)).to_contain_text("Last 30 days")

    va.goto("/accounts")
    row.locator(".accounts-pick").check()
    expect(va.page.locator("#accounts-bulk")).to_be_visible()
    va.page.once("dialog", lambda d: d.accept())
    with va.page.expect_navigation():
        va.page.click("#accounts-bulk-delete")
    expect(row).to_have_count(0)

    assert va.js_errors == []


def test_links_are_generated_for_a_models_accounts_and_deleted(admin: Session):
    admin.goto("/accounts")
    admin.page.click("#accounts-add-open")
    admin.page.fill("#accounts-add-handles", f"@{TRACKED_ACCOUNT}")
    admin.page.fill("#accounts-add-creator", "e2e model")
    admin.page.click('#accounts-add-form button[type="submit"]')
    account = admin.page.locator(
        "#accounts-table tbody tr", has_text=f"@{TRACKED_ACCOUNT}"
    )
    expect(account).to_have_count(1, timeout=SCAN_TIMEOUT_MS)

    admin.goto("/links")
    admin.page.click("#links-generate-open")
    picks = admin.page.locator("#links-gen-accounts label:not([hidden])")
    expect(picks).to_have_count(0)
    admin.page.select_option("#links-gen-creator", "e2e model")
    expect(picks).to_have_count(1)
    expect(picks.first.locator("input")).to_be_checked()
    admin.page.select_option("#links-gen-provider", "bouncy")
    expect(admin.page.locator("#links-gen-domain")).to_have_value("bouncy.ai")
    destination = admin.page.locator("#links-gen-destination")
    problem = admin.page.locator("#links-gen-destination-problem")
    admin.page.select_option("#links-gen-target", "stacked")
    expect(destination).to_have_value("https://www.stacked.com/@")
    admin.page.select_option("#links-gen-target", "fanvue")
    expect(destination).to_have_value("https://www.fanvue.com/")
    admin.page.click('#links-generate-form button[type="submit"]')
    expect(problem).to_have_text("Add the Fanvue profile after https://www.fanvue.com/")
    destination.fill("https://onlyfans.com/wrong-page")
    expect(problem).to_have_text("Not a Fanvue link")
    destination.fill("some bs")
    expect(problem).to_have_text("Paste the Fanvue profile link")
    destination.fill("https://www.fanvue.com/e2e")
    expect(problem).to_be_hidden()
    admin.page.click('#links-generate-form button[type="submit"]')

    row = admin.page.locator("#links-table tbody tr", has_text=f"@{TRACKED_ACCOUNT}")
    expect(row).to_have_count(1, timeout=SCAN_TIMEOUT_MS)
    expect(row).to_have_attribute("data-status", "active", timeout=SCAN_TIMEOUT_MS)
    expect(row).to_contain_text("bouncy.ai/")
    expect(row.locator('td[data-label="Model"]')).to_have_text("e2e model")
    expect(row).not_to_contain_text("No 18+")

    admin.goto("/accounts")
    expect(account.locator('td[data-label^="Links"]')).to_contain_text("Bouncy")

    admin.goto("/links")
    row.locator(".links-pick").check()
    expect(admin.page.locator("#links-bulk")).to_be_visible()
    admin.page.once("dialog", lambda d: d.accept())
    admin.page.click("#links-bulk-delete")
    expect(row).to_have_count(0, timeout=SCAN_TIMEOUT_MS)

    assert admin.js_errors == []


def test_admin_adds_a_phone_and_login_exports_it_and_a_user_never_sees_it(
    admin: Session, va: Session
):
    stamp = "4242"
    label = f"e2e {stamp}"
    username = f"e2e{stamp}@gmail.com"

    admin.goto("/phones")
    admin.page.click("#ph-add-open")
    admin.page.select_option("#ph-family", "iphone-12")
    admin.page.select_option("#ph-model", "iphone-12-mini")
    admin.page.locator(".ph-swatch", has_text="Purple").click()
    expect(admin.page.locator("#ph-drawing")).to_have_attribute(
        "src", "/phones/drawing.svg?model=iphone-12-mini&color=purple"
    )
    admin.page.fill("#ph-label", label)
    with admin.page.expect_navigation():
        admin.page.click('#ph-form button[type="submit"]')
    card = admin.page.locator(".ph-card", has_text=label)
    expect(card).to_have_count(1)
    expect(card.locator(".ph-specs")).to_have_text("iPhone 12 mini · Purple")
    expect(card.locator(".ph-media img")).to_be_visible()

    card.locator(".ph-add-login").click()
    admin.page.select_option("#ph-login-service", "gmail")
    admin.page.fill("#ph-login-username", username)
    admin.page.fill("#ph-login-password", "e2e-secret")
    with admin.page.expect_navigation():
        admin.page.click('#ph-login-form button[type="submit"]')

    assert "e2e-secret" not in admin.page.content()
    row = card.locator(".ph-login", has_text=username)
    secret = row.locator("[data-secret]")
    expect(secret).to_be_hidden()
    row.hover()
    row.locator(".ph-reveal").click()
    expect(secret).to_have_text("e2e-secret")
    row.locator(".ph-reveal").click()
    expect(secret).to_be_hidden()

    va.goto("/phones")
    expect(va.page.locator(".ph-card", has_text=label)).to_have_count(0)
    admin.page.fill("#ph-search", stamp)
    expect(admin.page.locator("#ph-grid .ph-card:visible")).to_have_count(1)
    with admin.page.expect_download() as download:
        admin.page.click('a[href="/phones/export.xlsx"]')
    assert re.fullmatch(
        r"phones-\d{4}-\d{2}-\d{2}\.xlsx", download.value.suggested_filename
    )
    download.value.delete()

    admin.goto("/phones")
    admin.page.on("dialog", lambda d: d.accept())
    card.locator(".menu summary").click()
    with admin.page.expect_navigation():
        card.locator(".ph-delete").click()
    expect(admin.page.locator(".ph-card", has_text=label)).to_have_count(0)

    assert admin.js_errors == []
    assert va.js_errors == []


def _seed_phone(label: str, owner: str, *logins: tuple[str, str]) -> int:
    device = devices.add_device(
        DeviceFields(label=label, model_id="iphone-13", color_id="blue", owner=owner)
    )
    for service, username in logins:
        assert devices.add_login(
            device, LoginFields(service=service, username=username)
        )
    return device


def _capture_json(session: Session, pattern: str) -> list[dict]:
    bodies: list[dict] = []

    def handle(route):
        response = route.fetch()
        bodies.append(response.json())
        route.fulfill(response=response)

    session.page.route(pattern, handle)
    return bodies


def test_drive_page_lists_accounts_searches_and_syncs(
    admin: Session, fake_drive: FakeDrive, jobs_idle
):
    connect_google(admin, "agency.google@example.com")
    jobs_idle()
    _seed_phone("Phone 01", "Anna", ("instagram", "ann.ig"), ("gmail", "ann@gmail.com"))
    _seed_phone("Phone 02", "Bob", ("instagram", "bob.ig"))
    retired = _seed_phone("Phone 03", "Old", ("instagram", "old.ig"))
    devices.retire_device(retired)

    admin.goto("/drive")
    expect(admin.page.locator("h1")).to_have_text("Drive")
    cards = admin.page.locator("#dr-grid .dr-card")
    visible = admin.page.locator("#dr-grid .dr-card:visible")
    expect(cards).to_have_count(2)
    expect(cards.filter(has_text="No folder yet")).to_have_count(2)

    for query, shown in (("bob", ["bob.ig"]), ("phone 01", ["ann.ig"]), ("zzz", [])):
        admin.page.fill("#dr-search", query)
        expect(visible).to_have_count(len(shown))
        for handle in shown:
            expect(visible.locator(".dr-account")).to_have_text(f"@{handle}")
    admin.page.fill("#dr-search", "")
    expect(visible).to_have_count(2)

    reports = _capture_json(admin, "**/drive/sync")
    with admin.page.expect_navigation():
        admin.page.click("#dr-sync")
    assert reports.pop() == {
        "created": ["ann.ig - Anna", "bob.ig - Bob"],
        "adopted": [],
        "renamed": [],
        "posted_folders": ["ann.ig - Anna/POSTED", "bob.ig - Bob/POSTED"],
        "not_connected": [],
        "newly_posted": 0,
        "failed": [],
        "busy": False,
    }
    expect(cards.filter(has_text="Nothing waiting to post")).to_have_count(2)

    root = fake_drive.children(my_root(fake_drive, "agency.google@example.com"))
    assert sorted(f.name for f in root) == ["ann.ig - Anna", "bob.ig - Bob"]
    for folder in root:
        assert [f.name for f in fake_drive.children(folder.id)] == ["POSTED"]
    assert fake_drive.writes == ["create"] * 5

    (ann,) = fake_drive.named(
        "ann.ig - Anna", my_root(fake_drive, "agency.google@example.com")
    )
    (ann_posted,) = fake_drive.named("POSTED", ann)
    reel = fake_drive.add_file(ann, "reel1.mp4", "video/mp4", thumbnail=True)
    with (
        admin.page.expect_response(re.compile(r"/drive/thumb/")) as preview,
        admin.page.expect_response(re.compile(r"/drive/live")) as live,
    ):
        admin.page.reload()
    assert preview.value.ok
    assert live.value.ok
    ann_card = cards.filter(has_text="ann.ig")
    expect(ann_card.locator(".dr-count")).to_have_text("Waiting: 1")
    expect(ann_card.locator(".dr-file img")).to_be_visible()

    fake_drive.move(reel, ann_posted)
    with admin.page.expect_navigation():
        admin.page.click("#dr-sync")
    assert reports.pop()["newly_posted"] == 1
    expect(ann_card.locator(".dr-uploaded")).to_contain_text(
        "Uploaded to Drive today: 1"
    )

    assert fake_drive.writes == ["create"] * 5
    assert admin.js_errors == []


def test_a_user_uploads_a_finished_clone_into_their_accounts_drive(
    va: Session, fake_drive: FakeDrive, source_videos: list[Path], jobs_idle
):

    connect_google(va, "va.google@example.com")
    expect(va.page.locator(".card").nth(1)).to_contain_text("Connected")
    jobs_idle()

    va_id = 2
    phone = devices.add_device(
        DeviceFields(
            label="Vee 01", model_id="iphone-13", color_id="blue", owner="Vee"
        ),
        user_id=va_id,
    )
    assert devices.add_login(phone, LoginFields(service="instagram", username="vee.ig"))
    va.goto("/drive")
    with va.page.expect_navigation():
        va.page.click("#dr-sync")

    video = source_videos[0]
    params = {
        "prompt": "e2e upload",
        "resolution": "720P",
        "audio": False,
        "duration": 3,
        "reference_images": [],
        "reference_videos": [str(video)],
    }
    job_id = create_job("clone", params, actor=str(va_id))
    run_job(job_id, lambda: [{"name": video.name, "path": str(video)}], {})

    va.goto("/clone")
    card = va.page.locator(f'#clone-gallery > [data-job-id="{job_id}"]')
    expect(card.locator(".result-actions .download-btn")).to_be_visible()
    card.locator(".menu summary").click()
    card.locator(".drive-upload-btn").click()
    va.page.wait_for_url(f"**/drive?upload={job_id}")
    expect(va.page.locator("#dr-pick video")).to_have_count(1)
    va.page.locator('.dr-card[data-folder="vee.ig - Vee"]').click()
    confirm = va.page.locator("#dr-pick-confirm")
    expect(confirm).to_contain_text("You are moving VID_")
    expect(confirm).to_contain_text("to vee.ig - Vee")
    va.page.locator("#dr-pick-submit").click()
    va.page.wait_for_url("**/clone*")
    card = va.page.locator(f'#clone-gallery > [data-job-id="{job_id}"]')
    done = card.locator('.drive-states li[data-status="done"]')
    expect(done).to_contain_text("In Drive: vee.ig - Vee", timeout=RESULT_TIMEOUT_MS)

    (folder,) = fake_drive.named(
        "vee.ig - Vee", my_root(fake_drive, "va.google@example.com")
    )
    uploaded = [n for n in fake_drive.names_in(folder) if n.startswith("VID_")]
    assert len(uploaded) == 1

    va.page.once("dialog", lambda d: d.accept())
    done.locator(".drive-remove").click()
    expect(card.locator(".drive-states li")).to_have_count(0)
    assert [n for n in fake_drive.names_in(folder) if n.startswith("VID_")] == []
    assert va.js_errors == []
