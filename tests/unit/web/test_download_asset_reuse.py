# Copyright (C) 2026 jpkrajewski

import io

import pytest
from PIL import Image, PngImagePlugin

from ofmhelpers.features.assets import store as task_helper_uploads
from ofmhelpers.features.downloads import clean as clean_image
from ofmhelpers.features.downloads import images as download_images
from ofmhelpers.features.downloads import videos as download_reels
from ofmhelpers.integrations.downloaders.generic import DownloadResult
from ofmhelpers.media import metadata_cleaner
from ofmhelpers.web.routers import task_helpers


def _png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), color="green").save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def assets_dir(tmp_path, monkeypatch):
    d = tmp_path / "assets"
    d.mkdir()
    monkeypatch.setattr(task_helper_uploads, "assets_root", lambda: d)
    return d


def _stored_names(assets_dir):
    return {p.name.split("__", 1)[1] for p in assets_dir.glob("*") if p.is_file()}


def test_downloaded_videos_land_in_the_asset_store(tmp_path, assets_dir, monkeypatch):
    downloaded = tmp_path / "reel.mp4"
    downloaded.write_bytes(b"pretend-video-bytes")

    monkeypatch.setattr(
        download_reels,
        "download_all",
        lambda urls, **_login: [
            DownloadResult(url=urls[0], success=True, output_paths=[downloaded]),
        ],
    )

    download_reels.run_downloads(["https://example.com/x"], str(assets_dir))

    assert "reel.mp4" in _stored_names(assets_dir)


def test_downloaded_images_land_in_the_asset_store(tmp_path, assets_dir, monkeypatch):
    downloaded = tmp_path / "shot.png"
    downloaded.write_bytes(_png_bytes())

    monkeypatch.setattr(
        download_images,
        "download_all",
        lambda urls, **_login: [
            DownloadResult(url=urls[0], success=True, output_paths=[downloaded]),
        ],
    )

    download_images.run_downloads(["https://example.com/y"], str(assets_dir))

    assert "shot.png" in _stored_names(assets_dir)


def test_cleaned_images_land_in_the_asset_store(tmp_path, assets_dir, monkeypatch):
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    (job_dir / "cleaned.png").write_bytes(_png_bytes())

    result = clean_image.run_clean(str(job_dir), str(assets_dir))

    assert [f.name for f in result] == ["IMG_0001.png"]
    assert "IMG_0001.png" in _stored_names(assets_dir)


def test_a_clean_returns_only_the_files_of_its_own_job(tmp_path, assets_dir):
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    (job_dir / "mine.png").write_bytes(_png_bytes())
    (job_dir / "stranger.png").write_bytes(_png_bytes())

    result = clean_image.run_clean(str(job_dir), str(assets_dir), ["mine.png"])

    assert [f.name for f in result] == ["IMG_0001.png"]
    assert (job_dir / "stranger.png").is_file()


def _png_with_text_chunk() -> bytes:
    info = PngImagePlugin.PngInfo()
    info.add_text("secret", "gps")
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), color="green").save(buf, format="PNG", pnginfo=info)
    return buf.getvalue()


def _crash_on_save(monkeypatch, nth: int, *, after_saving: bool) -> None:
    real_save = metadata_cleaner._save_clean_png
    calls = []

    def save(src, dst):
        calls.append(src.name)
        if len(calls) == nth and not after_saving:
            msg = "disk gone"
            raise OSError(msg)
        real_save(src, dst)
        if len(calls) == nth:
            msg = "process died"
            raise OSError(msg)

    monkeypatch.setattr(metadata_cleaner, "_save_clean_png", save)


@pytest.mark.parametrize("after_saving", [True, False])
def test_a_rerun_after_a_crash_returns_each_file_once_with_metadata_stripped(
    tmp_path,
    assets_dir,
    monkeypatch,
    after_saving,
):
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    for name in ("a.png", "b.png", "c.png"):
        (job_dir / name).write_bytes(_png_with_text_chunk())
    names = ["a.png", "b.png", "c.png"]
    with monkeypatch.context() as crashing:
        _crash_on_save(crashing, 2, after_saving=after_saving)
        with pytest.raises(OSError, match="disk gone|process died"):
            clean_image.run_clean(str(job_dir), str(assets_dir), names)

    result = clean_image.run_clean(str(job_dir), str(assets_dir), names)

    assert [f.name for f in result] == ["IMG_0001.png", "IMG_0002.png", "IMG_0003.png"]
    assert sorted(p.name for p in job_dir.glob("*.png")) == [f.name for f in result]
    for produced in result:
        with Image.open(job_dir / produced.name) as out:
            assert out.text == {}


def test_an_upload_named_like_a_cleaned_file_is_still_cleaned(tmp_path, assets_dir):
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    (job_dir / "IMG_0001.png").write_bytes(_png_with_text_chunk())

    result = clean_image.run_clean(str(job_dir), str(assets_dir), ["IMG_0001.png"])

    assert [f.name for f in result] == ["IMG_0002.png"]
    with Image.open(job_dir / "IMG_0002.png") as out:
        assert out.text == {}
    assert not (job_dir / "IMG_0001.png").exists()


def test_two_identical_uploads_give_two_outputs(tmp_path, assets_dir):
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    (job_dir / "a.png").write_bytes(_png_bytes())
    (job_dir / "b.png").write_bytes(_png_bytes())

    result = clean_image.run_clean(str(job_dir), str(assets_dir), ["a.png", "b.png"])

    assert [f.name for f in result] == ["IMG_0001.png", "IMG_0002.png"]


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
    vanished = tmp_path / "never-written.mp4"
    task_helpers.register_grouped_results(
        [DownloadResult(url="https://x/reel", success=True, output_paths=[vanished])],
        assets_dir,
    )

    assert list(assets_dir.glob("*")) == []
