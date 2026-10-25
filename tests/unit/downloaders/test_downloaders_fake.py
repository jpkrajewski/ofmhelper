"""OFM_FAKE_EXTERNALS: yt-dlp and gallery-dl are never run; a URL "downloads"
to real local media, and "fake-fail" fails like a removed post."""

import io

import pytest
from PIL import Image

from ofmhelpers.downloaders import generic, images, media
from ofmhelpers.downloaders.generic import DownloadConfig
from ofmhelpers.downloaders.images import ImageDownloadConfig
from ofmhelpers.utils import fake_media, outbound


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


# --- the look at a link agrees with the download (review #49) ----------------

REEL = "https://www.instagram.com/reel/ABC123/"


def _video_of(url: str, monkeypatch, tmp_path):
    """The fake download's first video of `url` (None: no video)."""
    monkeypatch.setattr(media, "_OUTPUT_ROOT", tmp_path / "downloads")
    with media.downloaded(url, single=True) as result:
        return next((p.name for p in result.output_paths if p.suffix == ".mp4"), None)


def test_a_reel_looks_like_the_2_s_video_it_downloads_to(monkeypatch, tmp_path):
    info = generic.video_info(REEL)

    assert info.seconds == fake_media.MP4_SECONDS
    assert info.error is None
    assert info.thumbnail is None
    assert _video_of(REEL, monkeypatch, tmp_path) is not None


@pytest.mark.parametrize(
    "url",
    [
        "https://www.instagram.com/p/PHOTO1/",
        "https://x.com/someone/status/1",
        "https://www.tiktok.com/@a/photo/1",
    ],
)
def test_a_photo_post_has_no_video_in_the_look_and_the_download(
    url, monkeypatch, tmp_path
):
    info = generic.video_info(url)

    assert info.error == "No video at this link (a photo post?)"
    assert info.seconds is None
    assert _video_of(url, monkeypatch, tmp_path) is None


def test_a_failing_url_fails_the_look_too():
    info = generic.video_info("https://www.instagram.com/reel/fake-fail-1/")

    assert info.error == "ERROR: Video unavailable"
    assert info.seconds is None


def test_fake_long_is_a_20_s_video():
    assert (
        generic.video_info("https://www.tiktok.com/@a/video/fake-long").seconds == 20.0
    )


def test_fake_login_says_the_site_wants_a_login():
    info = generic.video_info("https://www.instagram.com/reel/fake-login/")

    assert info.seconds is None
    assert "login required" in info.error


def test_fake_thumb_has_a_cover_the_fake_cdn_serves():
    info = generic.video_info("https://www.instagram.com/reel/fake-thumb-1/")

    assert info.seconds == fake_media.MP4_SECONDS
    response = outbound.get(info.thumbnail, timeout=5)
    assert response.status_code == 200
    assert response.headers["Content-Type"] == "image/jpeg"
    with Image.open(io.BytesIO(response.content)) as img:
        assert img.size == (320, 568)
