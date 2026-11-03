"""/trials/{login} in a real browser: the page never reloads, a click shows
on its card at once, Select + Upload asks first, then files exactly the
selected copies (the bug of 2026-10-30: the checkboxes were disabled before the
form was read, so the upload went out empty), a click spammed starts one job."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest
from e2e_session import Session, connect_google, my_root
from playwright.sync_api import expect
from sqlalchemy import create_engine, text

from ofmhelpers.gdrive.fake import FakeDrive
from ofmhelpers.trials.state import FolderState, ReelState
from ofmhelpers.web.schemas import DeviceFields, LoginFields
from ofmhelpers.web.stores import devices

pytestmark = pytest.mark.e2e

VA_ID = 2  # e2e conftest `_seed_users`
GOOGLE = "va.google@example.com"
_CAMERA_NAME = re.compile(r"VID_\d{4}\.mp4")


def _clip(path: Path) -> Path:
    """A real 1 s 9:16 video: the page makes its poster and preview."""
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc2=size=180x320:rate=10:duration=1",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path),
        ],
        check=True,
    )  # fmt: skip
    return path


def _folder_with_copies(va: Session, app_dir: Path, jobs_idle, count: int) -> int:
    """Their phone with @vee.ig, its Drive folder made by Sync, and `count`
    made copies in its trials state (the app's own uploads folder)."""
    connect_google(va, GOOGLE)
    jobs_idle()
    phone = devices.add_device(
        DeviceFields(
            label="Vee 01", model_id="iphone-13", color_id="blue", owner="Vee"
        ),
        user_id=VA_ID,
    )
    added = devices.add_login(
        phone, LoginFields(service="instagram", username="vee.ig")
    )
    assert added is not None
    login = added[0]
    va.goto("/drive")
    with va.page.expect_navigation():
        va.page.click("#dr-sync")
    jobs_idle()
    work = app_dir / "uploads" / "trials" / str(VA_ID) / str(login)
    (work / "out").mkdir(parents=True, exist_ok=True)
    clip = _clip(work / "clip.mp4")
    reels = []
    for i in range(count):
        key = f"key{i}"
        shutil.copy(clip, work / "out" / f"{key}.mp4")
        reels.append(
            ReelState(
                key=key,
                name=f"VID_100{i}.mp4",
                out=f"out/{key}.mp4",
                status="generated",
                caption=f"caption {i}",
                made=1,
            )
        )
    (work / "state.json").write_text(
        FolderState(login_id=login, reels=reels).model_dump_json(), encoding="utf-8"
    )
    return login


def test_selected_copies_upload_without_a_reload(
    va: Session, fake_drive: FakeDrive, app_dir: Path, jobs_idle
):
    login = _folder_with_copies(va, app_dir, jobs_idle, 3)
    va.goto(f"/trials/{login}")
    page = va.page
    page.evaluate("window.notReloaded = true")

    for key in ("key0", "key1"):
        page.locator(f'.tr-reel[data-key="{key}"] .tr-pick').click()
    upload = page.locator(".tr-upload-btn")
    expect(upload).to_contain_text("Upload 2 to Drive")
    upload.click()
    expect(page.locator("#tr-replace")).to_be_visible()
    expect(page.locator("#tr-replace")).to_contain_text("2 reels")
    page.locator("#tr-replace-go").click()
    expect(page.locator("#tr-replace")).to_be_hidden()

    for key in ("key0", "key1"):
        expect(page.locator(f'.tr-reel[data-key="{key}"]')).to_contain_text(
            "In Drive · VID_", timeout=30_000
        )
    expect(page.locator('.tr-reel[data-key="key2"] .tr-pick')).to_contain_text("Select")
    assert page.evaluate("window.notReloaded") is True
    (account,) = fake_drive.named("vee.ig - Vee", my_root(fake_drive, GOOGLE))
    (trials,) = fake_drive.named("TRIALS", account)
    names = fake_drive.names_in(trials)
    assert len(names) == 2
    assert all(_CAMERA_NAME.fullmatch(n) for n in names)
    # Reloaded: in Drive, nothing selected, all can join the next batch.
    va.goto(f"/trials/{login}")
    expect(page.locator(".tr-pick")).to_have_count(3)
    expect(upload).to_be_disabled()
    assert va.js_errors == []


def test_upload_cancelled_uploads_nothing_and_a_new_batch_replaces_the_old(
    va: Session, fake_drive: FakeDrive, app_dir: Path, jobs_idle
):
    login = _folder_with_copies(va, app_dir, jobs_idle, 2)
    va.goto(f"/trials/{login}")
    page = va.page
    dialog = page.locator("#tr-replace")
    upload = page.locator(".tr-upload-btn")
    key0 = page.locator('.tr-reel[data-key="key0"]')
    key1 = page.locator('.tr-reel[data-key="key1"]')

    key0.locator(".tr-pick").click()
    upload.click()
    expect(dialog).to_contain_text("TRIALS")
    expect(dialog).to_contain_text("1 reel")
    page.locator("#tr-replace-back").click()

    expect(dialog).to_be_hidden()
    expect(key0.locator(".tr-pick input")).to_be_checked()
    expect(key0.locator(".tr-video")).to_be_visible()
    jobs_idle()
    (account,) = fake_drive.named("vee.ig - Vee", my_root(fake_drive, GOOGLE))
    assert fake_drive.named("TRIALS", account) == []

    upload.click()
    page.locator("#tr-replace-go").click()
    expect(key0).to_contain_text("In Drive · VID_", timeout=30_000)
    (trials,) = fake_drive.named("TRIALS", account)
    (first,) = fake_drive.names_in(trials)

    key1.locator(".tr-pick").click()
    upload.click()
    page.locator("#tr-replace-go").click()
    expect(key1).to_contain_text("In Drive · VID_", timeout=30_000)
    jobs_idle()

    expect(key0).not_to_contain_text("In Drive", timeout=15_000)
    names = fake_drive.names_in(trials)
    assert len(names) == 1
    assert names != [first]
    assert va.js_errors == []


def test_generate_reads_posted_and_a_new_copy_lands_first_without_a_reload(
    va: Session, fake_drive: FakeDrive, app_dir: Path, jobs_idle
):
    """The fake Drive's files carry no bytes, so the job cannot make the reel it
    finds: it reads POSTED and says so. Then the copy lands the way a job writes
    it: the open page puts it first, in POSTED's order, on the next poll."""
    login = _folder_with_copies(va, app_dir, jobs_idle, 1)
    (account,) = fake_drive.named("vee.ig - Vee", my_root(fake_drive, GOOGLE))
    (posted,) = fake_drive.named("POSTED", account)
    fake_drive.add_file(posted, "new.mp4", "video/mp4", owner=GOOGLE)
    va.goto(f"/trials/{login}")
    page = va.page
    page.evaluate("window.notReloaded = true")

    page.locator("#tr-generate button").click()

    expect(page.locator("#tr-status")).to_have_text(
        "Could not make 1 new reel. Click Generate trials to try again.",
        timeout=30_000,
    )
    expect(page.locator("#tr-generate button")).to_be_enabled()
    jobs_idle()

    work = app_dir / "uploads" / "trials" / str(VA_ID) / str(login)
    state = FolderState.model_validate_json((work / "state.json").read_text())
    new = next(r for r in state.reels if r.name == "new.mp4")
    assert state.reels[0].key == new.key  # newest in POSTED, the old one undated
    shutil.copy(work / "out" / "key0.mp4", work / "out" / "new.mp4")
    new.out, new.status, new.caption, new.made = (
        "out/new.mp4",
        "generated",
        "new caption",
        1,
    )
    (work / "state.json").write_text(state.model_dump_json(), encoding="utf-8")

    page.locator('.tr-reel[data-key="key0"] form[action$="/regenerate"] button').click()

    cards = page.locator(".tr-reel")
    expect(cards).to_have_count(2, timeout=15_000)
    expect(cards.first).to_have_attribute("data-key", new.key)
    expect(cards.first.locator("textarea")).to_have_value("new caption")
    assert page.evaluate("window.notReloaded") is True
    assert va.js_errors == []


def test_choosing_a_caption_shows_on_its_card_at_once(
    va: Session, fake_drive: FakeDrive, app_dir: Path, jobs_idle
):
    login = _folder_with_copies(va, app_dir, jobs_idle, 1)
    work = app_dir / "uploads" / "trials" / str(VA_ID) / str(login)
    state = FolderState.model_validate_json((work / "state.json").read_text())
    state.reels[0].captions = ["caption 0", "two", "three", "four", "five"]
    (work / "state.json").write_text(state.model_dump_json(), encoding="utf-8")
    va.goto(f"/trials/{login}")
    card = va.page.locator('.tr-reel[data-key="key0"]')
    choices = card.locator(".tr-choice")
    expect(choices).to_have_count(5)
    expect(choices.first).to_be_disabled()  # the one on the copy

    choices.nth(2).click()

    expect(card.locator(".tr-pending")).to_be_visible()
    jobs_idle()
    # The job ends (this fake copy has no source to make it from): the card
    # comes back by itself, with the choice kept.
    expect(card.locator(".tr-video")).to_be_visible(timeout=15_000)
    expect(choices.nth(2)).to_have_attribute("aria-pressed", "true")
    assert va.js_errors == []


def test_regenerate_shows_at_once_and_ten_clicks_start_one_job(
    va: Session, fake_drive: FakeDrive, app_dir: Path, jobs_idle, e2e_backend
):
    login = _folder_with_copies(va, app_dir, jobs_idle, 1)
    va.goto(f"/trials/{login}")
    card = va.page.locator('.tr-reel[data-key="key0"]')
    button = card.locator("form[action$='/regenerate'] button")

    va.page.evaluate(
        "() => { const b = document.querySelector(\"form[action$='/regenerate'] button\");"
        " for (let i = 0; i < 10; i++) b.click(); }"
    )

    expect(card.locator(".tr-pending")).to_be_visible()
    expect(button).to_be_disabled()
    jobs_idle()
    with create_engine(e2e_backend.database_url).begin() as conn:
        started = conn.execute(
            text("SELECT count(*) FROM jobs WHERE task = 'trials'")
        ).scalar_one()
    assert started == 1
    # The job ends (this fake copy has no source to make it from): the card
    # comes back by itself, with its short reason.
    expect(card.locator(".tr-video")).to_be_visible(timeout=15_000)
    expect(button).to_be_enabled()
