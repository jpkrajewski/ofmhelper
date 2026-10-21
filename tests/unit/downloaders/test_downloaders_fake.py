"""OFM_FAKE_EXTERNALS: yt-dlp and gallery-dl are never run; a URL "downloads"
to real local media, and "fake-fail" fails like a removed post."""

import pytest

from ofmhelpers.downloaders import generic, images
from ofmhelpers.downloaders.generic import DownloadConfig
from ofmhelpers.downloaders.images import ImageDownloadConfig


@pytest.fixture(autouse=True)
def fakes_on(monkeypatch, tmp_path):
    monkeypatch.setenv("OFM_FAKE_EXTERNALS", "1")
    monkeypatch.setenv("OFM_FAKES_DIR", str(tmp_path / "fakes"))


def test_a_video_url_downloads_to_a_playable_mp4(tmp_path):
    result = generic.download(
        "https://www.tiktok.com/@a/video/1", DownloadConfig(output_dir=tmp_path / "v")
    )
    assert result.success
    (path,) = result.output_paths
    assert path.suffix == ".mp4"
    assert path.read_bytes()[4:8] == b"ftyp"


def test_a_post_url_downloads_two_images(tmp_path):
    result = images.download(
        "https://www.instagram.com/p/abc/",
        ImageDownloadConfig(output_dir=tmp_path / "i"),
    )
    assert result.success
    assert [p.suffix for p in result.output_paths] == [".jpg", ".jpg"]


def test_a_failing_url_fails_with_a_reason(tmp_path):
    result = generic.download(
        "https://www.tiktok.com/fake-fail", DownloadConfig(output_dir=tmp_path)
    )
    assert not result.success
    assert result.error == "ERROR: Video unavailable"
