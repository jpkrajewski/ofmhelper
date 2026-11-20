import os
import subprocess
lazy from pathlib import Path

import pytest
from PIL import Image
lazy from e2e_session import Session
lazy from playwright.sync_api import Response

from ofmhelpers.db.stores.jobs import create_job
from ofmhelpers.features.jobs import runner as jobs_store
from ofmhelpers.features.jobs.runner import run_job

pytestmark = pytest.mark.e2e

VIDEOS = 4
IMAGES = 4
MEDIA_BUDGET_SHARE = 0.05


def _video(path: Path) -> Path:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=2160x3840:rate=30:duration=1",
            "-c:v",
            "libx264",
            "-b:v",
            "28M",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
    )
    return path


def _image(path: Path) -> Path:
    Image.frombytes("RGB", (2048, 2048), os.urandom(2048 * 2048 * 3)).save(path)
    return path


@pytest.fixture
def heavy_gallery(tmp_path, monkeypatch) -> list[Path]:
    monkeypatch.setattr(jobs_store, "_queue_previews", lambda job_id: None)
    video, image = _video(tmp_path / "export.mp4"), _image(tmp_path / "gen.png")
    sources = [video] * VIDEOS + [image] * IMAGES
    for source in sources:
        job_id = create_job("fake_ai", {"prompt": "heavy"})
        run_job(job_id, lambda s=source: [{"name": s.name, "path": str(s)}], {})
    return sources


def test_a_heavy_gallery_loads_only_previews(admin: Session, heavy_gallery):
    media: list[Response] = []
    admin.page.on(
        "response",
        lambda r: r.request.resource_type in ("image", "media") and media.append(r),
    )

    admin.goto("/generate")
    cards = admin.page.locator("#results-panel .result-item")
    assert cards.count() == VIDEOS + IMAGES
    for i in range(cards.count()):
        cards.nth(i).scroll_into_view_if_needed()
    admin.page.wait_for_load_state("networkidle")

    videos = admin.page.locator("video.result-video")
    assert videos.count() == VIDEOS
    for i in range(VIDEOS):
        assert videos.nth(i).get_attribute("preload") == "none"
        assert videos.nth(i).get_attribute("poster").endswith("/poster")
    images = admin.page.locator("img.result-image")
    assert images.count() == IMAGES
    for i in range(IMAGES):
        assert images.nth(i).evaluate("img => img.complete && img.naturalWidth > 0")

    loaded = sum(len(r.body()) for r in media if r.ok)
    originals = sum(p.stat().st_size for p in heavy_gallery)
    assert not any(
        "/preview" in r.url and r.request.resource_type == "media" for r in media
    )
    assert loaded < originals * MEDIA_BUDGET_SHARE, (
        f"{loaded} B of media for {originals} B of results"
    )


def test_a_video_with_no_preview_yet_plays_its_original_url(
    admin: Session, heavy_gallery
):
    admin.goto("/generate")
    video = admin.page.locator("video.result-video").first
    src = video.get_attribute("src")
    assert "/files/" in src
    assert not src.endswith("/preview")

    with admin.page.expect_response(lambda r: r.url.endswith(src)) as played:
        video.evaluate("v => { v.muted = true; return v.play(); }")
    assert played.value.headers["content-type"] == "video/mp4"
    download = admin.page.locator(".download-btn").first.get_attribute("href")
    assert download.endswith("?dl=1")
