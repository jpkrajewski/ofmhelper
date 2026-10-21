"""End to end, in a real browser against the app the suite starts, every
outside service faked (see conftest.py):
log in, open every page, make a fake asset and download it, carry picked
files across providers' models, run a clone batch with a wrong kie.ai key, price a clone pick live,
click through every /reels filter and download three reels, track an
Instagram account on /accounts end to end, add a phone with a login on
/phones and export it."""

import re
import zipfile
from pathlib import Path

import pytest
from e2e_session import FAKE_DRIVE_ROOT, Session
from playwright.sync_api import expect

from ofmhelpers.gdrive.fake import FakeDrive
from ofmhelpers.web import drive_folders
from ofmhelpers.web.schemas import DeviceFields, LoginFields
from ofmhelpers.web.stores import devices, watched_profiles

pytestmark = pytest.mark.e2e

# Header order: the bar, then "More" (Uniqify, then the admin pages).
VA_PAGES = [
    "/generate",
    "/clone",
    "/reels",
    "/download-assets",
    "/accounts",
    "/phones",
    "/drive",
    "/uniqify",
]
ADMIN_ONLY_PAGES = [
    "/character-sheet",
    "/applications",
    "/file-manager",
    "/action-log",
    "/cookies",
]

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
        expect(admin.page.locator(".site-header nav")).to_be_visible()

    assert admin.js_errors == []


def test_va_sees_only_its_pages_and_none_of_the_admin_ones(va: Session):
    assert _nav_paths(va) == VA_PAGES

    for path in VA_PAGES:
        assert va.goto(path) == 200, path
        expect(va.page.locator(".site-header nav")).to_be_visible()
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
    va.page.select_option("#provider-select", "fake")
    va.page.select_option("#model-select", "fake:image")
    expect(va.page.locator("#api-key-field")).to_be_hidden()  # no key needed
    expect(va.page.locator('#model-fields [name="outcome"]')).to_be_visible()
    va.page.fill('#model-fields [name="delay"]', "0")
    va.page.fill('textarea[name="prompt"]', "e2e: a red studio backdrop")

    with va.page.expect_response(lambda r: r.url.endswith("/ai/run")) as run:
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


def _swap_fields(session: Session, pick) -> None:
    """Runs `pick` (a provider or model choice) and returns once the page
    has replaced the model fields it showed."""
    shown = session.page.locator("#model-fields").element_handle()
    pick()
    session.page.wait_for_function("el => !el.isConnected", arg=shown)


def test_picked_files_survive_a_model_switch(va: Session, subject_images: list[Path]):
    """Within a provider and across providers: the images go to the new
    model's image picker, whatever that field is called."""
    va.goto("/generate")
    # Each pick swaps in the model's fields when they arrive. Seedance 2.0
    # (the provider's default) renders the same picker as 2.5, so wait for
    # each swap, or a file can land on 2.0's just as it is replaced.
    _swap_fields(va, lambda: va.page.select_option("#provider-select", "kie"))
    _swap_fields(
        va, lambda: va.page.select_option("#model-select", "kie:bytedance/seedance-2-5")
    )
    seedance = '#model-fields .file-picker[data-field="reference_image_urls"]'
    for path in subject_images:  # a one-at-a-time picker, like a person
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


def test_clone_with_a_wrong_key_queues_the_batch_and_fails_cleanly(
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
    failed = va.page.locator(f"{batch} .result-file", has_text="kie.ai: wrong API key")
    expect(failed).to_have_count(len(source_videos), timeout=RESULT_TIMEOUT_MS)

    # Recreate brings the whole batch back without re-uploading.
    va.page.locator(f"{batch} .batch-recreate-btn").click()
    expect(va.page.locator('.file-picker[data-field="image"] li')).to_have_count(2)
    expect(va.page.locator('.file-picker[data-field="videos"] li')).to_have_count(2)

    assert va.js_errors == []
    assert coworker.js_errors == []


# Profiles of the Instagram fake (src/ofmhelpers/instagram/fake_profiles.json):
# once scanned, sheep_love33 keeps 5 reels (12k followers) and brookslockvip
# 4 (200k): within 14 days, 50k+ views, 15 s or shorter.
VIRAL_PROFILES = ["sheep_love33", "https://www.instagram.com/brookslockvip/"]
FEED_REELS = 9
SCAN_TIMEOUT_MS = 30_000


def test_clone_output_selects_and_switches_line_up(va: Session):
    """Each select sits beside its switch; both selects share one width and
    both switches one column, whatever the labels' lengths."""
    va.goto("/clone")
    row = va.page.locator(".clone-output-row")
    va.page.locator("#clone-export").focus()  # focus must keep the caret

    selects = [row.locator("select").nth(i).bounding_box() for i in range(2)]
    switches = [row.locator(".check-field").nth(i).bounding_box() for i in range(2)]

    assert selects[0]["width"] == selects[1]["width"]
    assert switches[0]["x"] == switches[1]["x"]
    assert va.page.eval_on_selector(
        "#clone-export", "e => getComputedStyle(e).backgroundImage"
    ).startswith("linear-gradient")
    assert va.js_errors == []


def test_clone_prices_the_picked_videos_live(va: Session, source_videos: list[Path]):
    # 3 s + 5 s of video at kie.ai's rates (32.78 / 64 credits a second).
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
    expect(va.page.locator("#clone-cost-usd")).to_have_text("$1.31")
    expect(va.page.locator("#clone-cost-credits")).to_have_text("262 credits")
    expect(va.page.locator("#clone-cost-rate")).to_have_text(
        "720P · $0.164 per video second"
    )

    va.page.select_option("#clone-resolution", "1080P")
    expect(va.page.locator("#clone-cost-usd")).to_have_text("$2.56")

    # Higgsfield: Wan 3.0 at 60% off, measured from a real run.
    va.page.select_option("#provider-select", "higgsfield")
    expect(va.page.locator("#clone-cost-usd")).to_have_text("$1.28")
    va.page.select_option("#clone-resolution", "720P")
    expect(va.page.locator("#clone-cost-usd")).to_have_text("$0.64")
    expect(va.page.locator("#clone-cost-credits")).to_have_text("10.2 credits")
    expect(va.page.locator("#clone-cost-rate")).to_have_text(
        "720P · $0.08 per video second"
    )
    expect(va.page.locator("#clone-cost-note")).to_contain_text("Higgsfield")
    va.page.select_option("#provider-select", "kie")
    va.page.select_option("#clone-resolution", "1080P")
    expect(va.page.locator("#clone-cost-usd")).to_have_text("$2.56")

    va.page.locator(
        '.file-picker[data-field="videos"] .file-order-list button', has_text="✕"
    ).first.click()
    expect(va.page.locator("#clone-cost-lines .cost-line")).to_have_count(1)
    expect(va.page.locator("#clone-cost-usd")).to_have_text("$1.60")


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
    expect(cards).to_have_count(FEED_REELS)
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
    picked_count = va.page.locator("#reels-picked-count")
    expect(picked_count).to_have_text(f"{len(picked)} reels selected")
    expect(va.page.locator("#reels-clone")).to_be_enabled()

    # The list and the picks survive a reload.
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
    body = run.value.json()
    assert len(body["job_ids"]) == len(picked)
    batch_id = re.search(r'data-batch="([^"]+)"', body["html"]).group(1)

    batch = f'#clone-batches > .clone-batch[data-batch="{batch_id}"]'
    # Higgsfield is the default provider; the wrong key fails on it.
    failed = va.page.locator(
        f"{batch} .result-file", has_text="Higgsfield: wrong API key"
    )
    expect(failed).to_have_count(len(picked), timeout=RESULT_TIMEOUT_MS)

    assert va.js_errors == []


def _add_viral_profiles(session: Session) -> None:
    """VIRAL_PROFILES on the watch list and scanned (a no-op once they are)."""
    session.goto("/reels")
    session.page.fill("#reels-profiles", "\n".join(VIRAL_PROFILES))
    session.page.click('#reels-form button[type="submit"]')
    expect(session.page.locator("#reels-form .generation-status")).to_contain_text(
        "Added", timeout=SCAN_TIMEOUT_MS
    )
    expect(session.page.locator(".reels-watch-item[data-pending]")).to_have_count(
        0, timeout=SCAN_TIMEOUT_MS
    )


# Cards each chip keeps, from the fake's numbers: tiers by views (legendary
# 1M+, epic 500k+, rare 200k+, uncommon 100k+, common 50k+); reach = views
# per follower (sheep 125x 52x 22x 11x 5.1x; brooks 11x 4.5x 1.6x 0.35x).
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
    """The chip's value: its group, tier or ratio from the href, else its
    label (the reset chip)."""
    query = (
        dict(re.findall(r"(\w+)=([^&]+)", href.split("?", 1)[-1]))
        if "?" in href
        else {}
    )
    return query.get("group") or query.get("rank") or query.get("ratio") or label


def test_every_reels_filter_chip_shows_exactly_its_reels(va: Session):
    _add_viral_profiles(va)
    watched_profiles.set_group("brookslockvip", "vip")
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

    # Clean up after ourselves: the download, and the picks.
    archive.unlink()
    download.value.delete()
    assert not archive.exists()
    for i in range(3):
        cards.nth(i).click()
    expect(va.page.locator("#reels-picked-count")).to_have_text("No reels selected")


# A small account of the Instagram fake (850 followers).
TRACKED_ACCOUNT = "yuki_kanasiva"


def test_tracked_account_is_scanned_annotated_and_removed(va: Session):
    va.goto("/accounts")
    va.page.click("#accounts-add-open")
    va.page.fill("#accounts-add-handles", f"@{TRACKED_ACCOUNT}")
    va.page.fill("#accounts-add-creator", "e2e creator")
    va.page.click('#accounts-add-form button[type="submit"]')

    row = va.page.locator("#accounts-table tbody tr", has_text=f"@{TRACKED_ACCOUNT}")
    expect(row).to_have_count(1, timeout=SCAN_TIMEOUT_MS)
    # The first scan runs on the worker; the page reloads itself until done.
    expect(row).to_have_attribute(
        "data-status", re.compile("active|private"), timeout=SCAN_TIMEOUT_MS
    )
    expect(row.locator(".accounts-creator-tag")).to_have_text("e2e creator")
    expect(row.locator('td[data-label="Followers"]')).to_contain_text("850")

    # The Phone column is filled from /phones logins, never typed here.
    expect(row.locator('td[data-label="Phone"]')).to_be_visible()

    # Search filters the rows instantly, without a reload.
    va.page.fill("#accounts-search", TRACKED_ACCOUNT[:6])
    expect(row).to_be_visible()
    va.page.fill("#accounts-search", "zzzz-no-such")
    expect(row).to_be_hidden()
    va.page.fill("#accounts-search", "")

    # Filtered by its creator, it is the only row.
    va.page.select_option('select[name="creator"]', "e2e creator")
    expect(va.page.locator("#accounts-table tbody tr")).to_have_count(1)

    with va.page.expect_navigation():
        row.locator(".accounts-go a").click()
    expect(va.page.locator(".accounts-identity")).to_contain_text(f"@{TRACKED_ACCOUNT}")
    kpis = va.page.locator(".accounts-kpi")
    expect(kpis).to_have_count(4)
    expect(kpis.first).to_contain_text("Followers")
    # The period selector reloads the page for that period.
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


def test_admin_adds_a_phone_and_login_va_views_and_exports(admin: Session, va: Session):
    stamp = "4242"
    label = f"e2e {stamp}"
    username = f"e2e{stamp}@gmail.com"

    admin.goto("/phones")
    admin.page.click("#ph-add-open")
    admin.page.select_option("#ph-family", "iphone-12")
    admin.page.select_option("#ph-model", "iphone-12-mini")
    admin.page.locator(".ph-swatch", has_text="Purple").click()
    # The drawing follows the picked model and color.
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

    # Masked until asked: the page itself never holds the password.
    assert "e2e-secret" not in admin.page.content()
    row = card.locator(".ph-login", has_text=username)
    secret = row.locator("[data-secret]")
    expect(secret).to_be_hidden()
    row.hover()
    row.locator(".ph-reveal").click()
    expect(secret).to_have_text("e2e-secret")
    row.locator(".ph-reveal").click()
    expect(secret).to_be_hidden()

    # A VA sees it, cannot change it, and downloads the Excel file.
    va.goto("/phones")
    va_card = va.page.locator(".ph-card", has_text=label)
    expect(va_card).to_contain_text(username)
    expect(va.page.locator("#ph-add-open")).to_have_count(0)
    expect(va_card.locator(".ph-menu")).to_have_count(0)
    va.page.fill("#ph-search", stamp)
    expect(va.page.locator("#ph-grid .ph-card:visible")).to_have_count(1)
    with va.page.expect_download() as download:
        va.page.click('a[href="/phones/export.xlsx"]')
    assert re.fullmatch(
        r"phones-\d{4}-\d{2}-\d{2}\.xlsx", download.value.suggested_filename
    )
    download.value.delete()

    # Clean up through the menu: delete the phone (its only login goes too).
    admin.goto("/phones")
    admin.page.on("dialog", lambda d: d.accept())
    card.locator(".ph-menu summary").click()
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
    """Bodies of the JSON answers to `pattern`, kept even when the page
    navigates away before the test could read them."""
    bodies: list[dict] = []

    def handle(route):
        response = route.fetch()
        bodies.append(response.json())
        route.fulfill(response=response)

    session.page.route(pattern, handle)
    return bodies


def test_drive_page_lists_accounts_searches_and_syncs(
    va: Session, fake_drive: FakeDrive
):
    _seed_phone("Phone 01", "Anna", ("instagram", "ann.ig"), ("gmail", "ann@gmail.com"))
    _seed_phone("Phone 02", "Bob", ("instagram", "bob.ig"))
    retired = _seed_phone("Phone 03", "Old", ("instagram", "old.ig"))
    devices.retire_device(retired)
    day = drive_folders.today().isoformat()

    va.goto("/drive")
    expect(va.page.locator("h1")).to_have_text(f"Drive · {day}")
    cards = va.page.locator("#dr-grid .dr-card")
    visible = va.page.locator("#dr-grid .dr-card:visible")
    expect(cards).to_have_count(2)  # Instagram logins on active phones only
    expect(cards.filter(has_text="No folder for today yet")).to_have_count(2)

    # Search: handle, owner, phone label; instantly, no reload.
    for query, shown in (("bob", ["bob.ig"]), ("phone 01", ["ann.ig"]), ("zzz", [])):
        va.page.fill("#dr-search", query)
        expect(visible).to_have_count(len(shown))
        for handle in shown:
            expect(visible.locator("h2")).to_have_text(handle)
    va.page.fill("#dr-search", "")
    expect(visible).to_have_count(2)

    reports = _capture_json(va, "**/drive/sync")
    with va.page.expect_navigation():  # the page reloads itself once done
        va.page.click("#dr-sync")
    assert reports.pop() == {
        "created": ["ann.ig - Anna", "bob.ig - Bob"],
        "adopted": [],
        "renamed": [],
        "days": 2,
        "failed": [],
        "busy": False,
    }
    expect(cards.filter(has_text="Nothing uploaded today")).to_have_count(2)

    # Exactly one folder per account and one day folder in each, nothing else.
    root = fake_drive.children(FAKE_DRIVE_ROOT)
    assert sorted(f.name for f in root) == ["ann.ig - Anna", "bob.ig - Bob"]
    for folder in root:
        assert [f.name for f in fake_drive.children(folder.id)] == [day]
    assert fake_drive.writes == ["create"] * 4

    # What the VA uploads shows up, with its preview through the app.
    (ann,) = fake_drive.named("ann.ig - Anna", FAKE_DRIVE_ROOT)
    (ann_day,) = fake_drive.named(day, ann)
    fake_drive.add_file(ann_day, "reel1.mp4", "video/mp4", thumbnail=True)
    with va.page.expect_response(re.compile(r"/drive/thumb/")) as preview:
        va.page.reload()
    assert preview.value.ok
    ann_card = cards.filter(has_text="ann.ig")
    expect(ann_card.locator(".dr-count")).to_have_text("1 video")
    expect(ann_card.locator(".dr-file img")).to_be_visible()
    expect(va.page.locator(".lead")).to_have_text(
        "1 of 2 accounts uploaded today · 1 file"
    )

    # A second Sync finds everything there and writes nothing.
    with va.page.expect_navigation():
        va.page.click("#dr-sync")
    assert reports.pop()["created"] == []
    assert fake_drive.writes == ["create"] * 4
    assert va.js_errors == []
