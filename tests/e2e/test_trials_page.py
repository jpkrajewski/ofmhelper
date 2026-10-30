"""/trials/{login} in a real browser: the page never reloads, a click shows
on its card at once, Select + Upload files exactly the selected copies
(the bug of 2026-10-30: the checkboxes were disabled before the form was
read, so the upload went out empty), a click spammed starts one job."""

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
    # Reloaded: in Drive, nothing to select or upload again.
    va.goto(f"/trials/{login}")
    expect(page.locator(".tr-pick")).to_have_count(1)
    expect(upload).to_be_disabled()
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
