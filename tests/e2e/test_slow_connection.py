"""A gallery of heavy results must cost a slow connection kilobytes, not the
results: /clone's 4K exports (~28 Mbit/s) and /generate's multi-MB PNGs never
showed for a partner on a slow line (prod, 2026-10-21). In a real browser:
the page loads posters and small previews only, no video starts loading
before it is played, and Download still gets the original."""

from __future__ import annotations

import os
import subprocess
from typing import TYPE_CHECKING

import pytest
from PIL import Image

from ofmhelpers.web.stores import jobs as jobs_store
from ofmhelpers.web.stores.jobs import create_job, run_job

if TYPE_CHECKING:
    from pathlib import Path

    from e2e_session import Session
    from playwright.sync_api import Response

pytestmark = pytest.mark.e2e

VIDEOS = 4
IMAGES = 4
# What the gallery may transfer in media, as a share of its results. The test
# images are noise (webp's worst case); real generations compress far better.
MEDIA_BUDGET_SHARE = 0.05


def _video(path: Path) -> Path:
    """1 s of 4K at the bitrate of a real export."""
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
    """A noisy 2048x2048 PNG: incompressible, like a real generation."""
    Image.frombytes("RGB", (2048, 2048), os.urandom(2048 * 2048 * 3)).save(path)
    return path


@pytest.fixture
def heavy_gallery(tmp_path, monkeypatch) -> list[Path]:
    """Finished jobs from before previews existed: nothing made ahead, so
    the page's own requests make them."""
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
    # Every image shown is a decoded preview, not a broken one.
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


def test_a_played_video_streams_its_preview(admin: Session, heavy_gallery):
    admin.goto("/generate")
    video = admin.page.locator("video.result-video").first
    src = video.get_attribute("src")
    assert src.endswith("/preview")

    with admin.page.expect_response(lambda r: r.url.endswith(src)) as played:
        video.evaluate("v => { v.muted = true; return v.play(); }")
    assert played.value.headers["content-type"] == "video/mp4"
    # Download is still the original.
    download = admin.page.locator(".download-btn").first.get_attribute("href")
    assert download.endswith("?dl=1")
