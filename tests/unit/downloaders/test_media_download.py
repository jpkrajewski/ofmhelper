"""One Download tool for any link: each URL tries yt-dlp and gallery-dl in
the order its kind of post suggests, and keeps the first that gets files."""

import pytest

from ofmhelpers.downloaders import generic, images, media
from ofmhelpers.downloaders.models import DownloadResult


@pytest.fixture
def tried(monkeypatch) -> list[str]:
    """Which downloader ran, in order; each fails unless the URL names it."""
    calls: list[str] = []

    def fake(name: str):
        def run(url: str, config=None) -> DownloadResult:
            calls.append(name)
            ok = name in url
            return DownloadResult(
                url=url,
                success=ok,
                output_paths=[f"/x/{name}.bin"] if ok else [],
                error=None if ok else f"{name} refused",
            )

        return run

    monkeypatch.setattr(generic, "download", fake("video"))
    monkeypatch.setattr(images, "download", fake("images"))
    return calls


@pytest.mark.parametrize(
    "url",
    [
        "https://www.tiktok.com/@a/video/1",
        "https://www.instagram.com/reel/abc/",
        "https://www.youtube.com/shorts/abc",
    ],
)
def test_video_links_try_yt_dlp_first(tried, url):
    media.download(url)
    assert tried[0] == "video"


@pytest.mark.parametrize(
    "url",
    [
        "https://www.instagram.com/p/abc/",
        "https://www.instagram.com/someone/p/abc/",
        "https://x.com/user/status/1",
        "https://twitter.com/user/status/1",
        "https://www.pinterest.com/pin/1/",
        "https://pin.it/abc",
        "https://www.tiktok.com/@a/photo/1",
    ],
)
def test_photo_post_links_try_gallery_dl_first(tried, url):
    media.download(url)
    assert tried[0] == "images"


def test_a_video_link_that_is_a_photo_post_falls_back_to_gallery_dl(tried):
    result = media.download("https://www.tiktok.com/@a/1234?images")

    assert result.success
    assert tried == ["video", "images"]
    assert [str(p) for p in result.output_paths] == ["/x/images.bin"]


def test_the_first_success_wins_and_the_other_is_not_run(tried):
    assert media.download("https://www.instagram.com/p/abc/images").success
    assert tried == ["images"]


def test_when_both_fail_both_reasons_are_kept(tried):
    result = media.download("https://example.com/nothing")

    assert not result.success
    assert result.error == "video: video refused | images: images refused"


def test_download_all_keeps_the_order(tried):
    urls = ["https://a.example/video", "https://www.instagram.com/p/images"]
    assert [r.url for r in media.download_all(urls)] == urls
