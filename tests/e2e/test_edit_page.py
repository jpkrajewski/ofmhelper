"""/edit in the browser (static/js/edit.js): frame steps through the video's
own frame times, the crop box and the turn as the size shown, and a click
on the picture that plays without dropping the last cut. The server side
(what Frame and Cut make) is tests/integration/web/test_edit.py."""

import re
import subprocess
from itertools import pairwise
from pathlib import Path

import pytest
from e2e_session import Session
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e

# The light copy and a cut are jobs on the edit worker, polled.
JOB_TIMEOUT_MS = 60_000
TIMES = chr(0xD7)  # the multiplication sign of the size shown: 1440 x 1080


@pytest.fixture
def clip(tmp_path: Path) -> Path:
    """4 s, 320x240 (landscape), 25 fps."""
    path = tmp_path / "clip.mp4"
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
            "4",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
        timeout=60,
    )
    return path


def _open(va: Session, clip: Path) -> None:
    """Uploads `clip` on /edit and waits for its light copy in the player."""
    va.goto("/edit")
    va.page.locator(".file-picker .file-add-input").set_input_files(str(clip))
    va.page.wait_for_url(re.compile(r"/edit\?video="))
    va.page.wait_for_function(
        "() => document.getElementById('edit-player').currentSrc.includes('/edit/proxy')"
        " && document.getElementById('edit-player').readyState >= 2",
        timeout=JOB_TIMEOUT_MS,
    )


def _marker(va: Session) -> str:
    return va.page.locator("#edit-at").inner_text()


def _timecode(va: Session) -> tuple[int, int]:
    """(seconds, frame within the second) of the marker."""
    _, seconds, frame = _marker(va).split(":")
    return int(seconds), int(frame)


def test_each_frame_step_moves_the_marker_by_one_frame(va: Session, clip: Path):
    _open(va, clip)
    strip = va.page.locator("#edit-timeline")
    width = strip.bounding_box()["width"]  # type: ignore[index]
    strip.click(position={"x": width * 0.3, "y": 10})  # a press: the marker
    expect(va.page.locator("#edit-at")).not_to_have_text("00:00:00")
    seen = [_timecode(va)]

    for _ in range(4):
        before = _marker(va)
        va.page.click(".edit-next")
        expect(va.page.locator("#edit-at")).not_to_have_text(before)
        seen.append(_timecode(va))
    before = _marker(va)
    va.page.keyboard.press("Shift+ArrowRight")
    expect(va.page.locator("#edit-at")).not_to_have_text(before)

    steps = [b[0] * 25 + b[1] - (a[0] * 25 + a[1]) for a, b in pairwise(seen)]
    assert steps == [1, 1, 1, 1]
    assert _timecode(va)[0] == seen[-1][0] + 1
    assert va.js_errors == []


def test_the_crop_box_and_the_turn_change_the_size_it_comes_out_at(
    va: Session, clip: Path
):
    _open(va, clip)
    size = va.page.locator("#edit-size")
    expect(size).to_have_text(f"1440{TIMES}1080")

    va.page.click(".edit-rotate")
    expect(size).to_have_text(f"1080{TIMES}1440")
    va.page.click(".edit-aspect[data-aspect='1:1']")
    expect(size).to_have_text(f"1080{TIMES}1080")
    box = va.page.locator("#edit-crop")
    box.scroll_into_view_if_needed()
    full = box.bounding_box()["width"]  # type: ignore[index]
    grip = va.page.locator(".edit-crop-grip[data-corner='se']").bounding_box()
    assert grip is not None
    va.page.mouse.move(grip["x"] + 5, grip["y"] + 5)
    va.page.mouse.down()
    va.page.mouse.move(grip["x"] - full / 2, grip["y"] - full / 2, steps=5)
    va.page.mouse.up()

    assert box.bounding_box()["width"] < full * 0.7  # type: ignore[index]
    va.page.click(".edit-size-pick[data-size='2160']")
    expect(size).to_have_text(f"2160{TIMES}2160")
    assert va.js_errors == []


def test_a_click_on_the_picture_plays_and_keeps_the_last_cut(va: Session, clip: Path):
    _open(va, clip)
    va.page.click("#edit-cut")
    download = va.page.locator("#edit-download")
    expect(download).to_be_visible(timeout=JOB_TIMEOUT_MS)

    va.page.locator("#edit-crop").click()  # the box covers the whole picture

    expect(va.page.locator(".edit-pause")).to_be_visible()
    expect(download).to_be_visible()
    assert va.js_errors == []
