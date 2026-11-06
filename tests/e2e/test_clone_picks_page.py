"""/clone's one-click picks and Start dialog (clone-picks.js), and Trim it!
-> /edit -> Clone it (clone-cost.js, edit.js, clone-return.js), in a real
browser against the e2e app (every outside service faked, the edit worker
running)."""

import re
import subprocess
from pathlib import Path

import pytest
from e2e_session import Session
from playwright.sync_api import Request, expect

pytestmark = pytest.mark.e2e

# A cut is a job on the edit worker: queued, encoded, polled every 2 s.
CUT_TIMEOUT_MS = 60_000
DEFAULT_PROMPT = "Replace the subject in @Video1 with subject from @Image1"


@pytest.fixture
def long_video(tmp_path: Path) -> Path:
    """16 s: one over Wan 3.0's 15 s, so /clone offers Trim it!."""
    path = tmp_path / "long.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=320x240:rate=25",
            "-t",
            "16",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
        timeout=60,
    )
    return path


def _picker(va: Session, field: str):
    return va.page.locator(f'.file-picker[data-field="{field}"]')


def test_a_chip_adds_its_words_and_takes_them_back_out(va: Session):
    va.goto("/clone")
    prompt = va.page.locator("#clone-prompt")
    chip = va.page.locator(".clone-form .addon-chip", has_text="No tattoos")

    chip.click()
    expect(prompt).to_have_value(f"{DEFAULT_PROMPT}, no tattoos")
    expect(chip).to_have_attribute("aria-pressed", "true")
    chip.click()
    expect(prompt).to_have_value(DEFAULT_PROMPT)
    expect(chip).to_have_attribute("aria-pressed", "false")
    # Typed in any wording, the chip lights up too.
    prompt.fill(f"{DEFAULT_PROMPT}, NO TATTOS")
    expect(chip).to_have_attribute("aria-pressed", "true")
    assert va.js_errors == []


def test_start_asks_first_and_a_tile_then_picks_that_one_subject(
    va: Session, subject_images: list[Path], source_videos: list[Path]
):
    va.goto("/clone")
    va.page.select_option("#provider-select", "kie")
    va.page.fill("#api-key", "e2e-wrong-key")
    _picker(va, "image").locator(".file-add-input").set_input_files(
        [str(p) for p in subject_images]
    )
    _picker(va, "videos").locator(".file-add-input").set_input_files(
        [str(source_videos[0])]
    )
    runs: list[str] = []

    def record(request: Request) -> None:
        if request.url.endswith("/clone/run"):
            runs.append(request.url)

    va.page.on("request", record)

    va.page.click('#clone-form button[type="submit"]')
    dialog = va.page.locator("#clone-confirm")
    expect(dialog).to_be_visible()
    expect(dialog.locator("#clone-confirm-title")).to_have_text("Start 1 clone?")
    expect(dialog.locator(".clone-confirm-images img")).to_have_count(2)
    expect(dialog.locator(".clone-confirm-videos video")).to_have_count(1)
    expect(dialog.locator("#clone-confirm-prompt mark")).to_have_count(2)
    # A chip in the dialog changes the prompt it shows and the form's.
    dialog.locator(".addon-chip", has_text="Remove text overlay").click()
    expect(dialog.locator("#clone-confirm-prompt")).to_contain_text(
        "remove text overlay"
    )
    expect(va.page.locator("#clone-prompt")).to_have_value(
        f"{DEFAULT_PROMPT}, remove text overlay"
    )
    dialog.locator('button[value="cancel"]').click()
    expect(dialog).to_be_hidden()
    assert runs == []

    with va.page.expect_response(lambda r: r.url.endswith("/clone/run")) as run:
        va.page.click('#clone-form button[type="submit"]')
        va.page.click("#clone-confirm-go")
    assert len(run.value.json()["job_ids"]) == 1
    assert run.value.request.post_data_buffer is not None
    assert b"remove text overlay" in run.value.request.post_data_buffer

    # The run's first image is now a tile (last used and most used).
    va.goto("/clone")
    tiles = va.page.locator(".subject-tile")
    expect(tiles).to_have_count(2)
    _picker(va, "image").locator(".file-add-input").set_input_files(
        [str(p) for p in subject_images]
    )
    tiles.first.click()
    expect(_picker(va, "image").locator(".file-order-list li")).to_have_count(1)
    expect(tiles.first).to_have_attribute("aria-pressed", "true")
    assert va.js_errors == []


def test_trim_it_cuts_the_long_video_and_clone_it_brings_the_form_back(
    va: Session,
    subject_images: list[Path],
    source_videos: list[Path],
    long_video: Path,
):
    va.goto("/clone")
    va.page.select_option("#provider-select", "kie")
    _picker(va, "image").locator(".file-add-input").set_input_files(
        [str(subject_images[0])]
    )
    va.page.locator(".clone-form .addon-chip", has_text="No tattoos").click()
    va.page.check('[name="mirror"]')
    _picker(va, "videos").locator(".file-add-input").set_input_files(
        [str(source_videos[0]), str(long_video)]
    )
    trim = va.page.locator("#clone-cost-lines .trim-btn")
    expect(trim).to_have_count(1)

    trim.click()
    va.page.wait_for_url(re.compile(r"/edit\?video="))
    expect(va.page.locator("#edit-length")).to_have_text("15.0 s")
    va.page.click("#edit-cut")
    clone_it = va.page.locator("#edit-clone")
    expect(clone_it).to_be_visible(timeout=CUT_TIMEOUT_MS)
    expect(va.page.locator("#edit-download")).to_be_visible()

    clone_it.click()
    va.page.wait_for_url(re.compile(r"/clone\?video=.*&from="))
    videos = _picker(va, "videos").locator(".file-order-list .item-name")
    expect(videos).to_have_count(2)
    expect(videos.nth(0)).to_contain_text("dance.mp4")
    expect(videos.nth(1)).to_contain_text("long_cut.mp4")
    expect(_picker(va, "image").locator(".file-order-list li")).to_have_count(1)
    expect(va.page.locator("#clone-prompt")).to_have_value(
        f"{DEFAULT_PROMPT}, no tattoos"
    )
    expect(va.page.locator('[name="mirror"]')).to_be_checked()
    # Every video now fits: no Trim it! left, both priced.
    expect(va.page.locator("#clone-cost-lines .trim-btn")).to_have_count(0)
    expect(va.page.locator("#clone-cost-summary")).to_have_text(
        "2 runs · 18.0 s of video"
    )
    assert va.js_errors == []
