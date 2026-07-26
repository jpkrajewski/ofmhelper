"""
Downloaded/cleaned output lands in the shared asset store, so it shows up in
the "reuse an uploaded image/video" pickers without a download-then-reupload
round trip (see task_helpers.register_generated_asset).
"""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ["APP_PASSWORD_VA"] = "test-va"
os.environ.setdefault("SESSION_SECRET", "test-secret")

import io

import pytest
from PIL import Image

from ofmhelpers.downloaders.generic import DownloadResult
from ofmhelpers.web.routers import (
    clean_image,
    download_images,
    download_reels,
    task_helpers,
)


def _png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), color="green").save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def assets_dir(tmp_path, monkeypatch):
    d = tmp_path / "assets"
    d.mkdir()
    monkeypatch.setattr(task_helpers, "ASSETS_ROOT", d)
    return d


def _stored_names(assets_dir):
    return {p.name.split("__", 1)[1] for p in assets_dir.glob("*") if p.is_file()}


def test_downloaded_videos_land_in_the_asset_store(tmp_path, assets_dir, monkeypatch):
    downloaded = tmp_path / "reel.mp4"
    downloaded.write_bytes(b"pretend-video-bytes")

    monkeypatch.setattr(
        download_reels,
        "download_all",
        lambda urls: [
            DownloadResult(url=urls[0], success=True, output_paths=[downloaded])
        ],
    )

    download_reels._run_downloads(["https://example.com/x"])

    assert "reel.mp4" in _stored_names(assets_dir)


def test_downloaded_images_land_in_the_asset_store(tmp_path, assets_dir, monkeypatch):
    downloaded = tmp_path / "shot.png"
    downloaded.write_bytes(_png_bytes())

    monkeypatch.setattr(
        download_images,
        "download_all",
        lambda urls: [
            DownloadResult(url=urls[0], success=True, output_paths=[downloaded])
        ],
    )

    download_images._run_downloads(["https://example.com/y"])

    assert "shot.png" in _stored_names(assets_dir)


def test_cleaned_images_land_in_the_asset_store(tmp_path, assets_dir, monkeypatch):
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    (job_dir / "cleaned.png").write_bytes(_png_bytes())
    monkeypatch.setattr(clean_image, "clean_metadata", lambda d: None)

    result = clean_image._run_clean(str(job_dir))

    assert [f["name"] for f in result] == ["cleaned.png"]
    assert "cleaned.png" in _stored_names(assets_dir)


def test_registering_the_same_content_twice_dedupes(tmp_path, assets_dir):
    a = tmp_path / "a.png"
    a.write_bytes(_png_bytes())
    b = tmp_path / "b-same-content.png"
    b.write_bytes(a.read_bytes())

    first = task_helpers.register_generated_asset(a, assets_dir)
    second = task_helpers.register_generated_asset(b, assets_dir)

    assert first == second
    assert len(list(assets_dir.glob("*"))) == 1


def test_a_missing_output_path_does_not_fail_the_job(tmp_path, assets_dir):
    """register_grouped_results is bookkeeping after the real work finished --
    a vanished file must not turn a successful download into a failure."""
    task_helpers.register_grouped_results(
        [{"output_paths": [str(tmp_path / "never-written.mp4")]}]
    )

    assert list(assets_dir.glob("*")) == []
