"""
fetch_source: a local file is copied in directly; a URL goes through
downloaders.generic.download. A failed Instagram download must surface a
hint pointing at /cookies (Instagram blocks logged-out downloads for most
reels) so the failure is actionable from the job's error message alone,
not just a raw yt-dlp error.
"""

from unittest import mock

import pytest

from ofmhelpers.downloaders.generic import DownloadResult
from ofmhelpers.reel_machine.intake import fetch_source


def test_local_file_is_copied_into_the_work_dir(tmp_path):
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    src_file = src_dir / "clip.mov"
    src_file.write_bytes(b"fake video bytes")

    work_dir = tmp_path / "work"
    result = fetch_source(str(src_file), work_dir)

    assert result == work_dir / "reference.mov"
    assert result.read_bytes() == b"fake video bytes"


def test_failed_download_raises_with_the_downloader_error(tmp_path):
    with mock.patch(
        "ofmhelpers.reel_machine.intake.download",
        return_value=DownloadResult(
            url="https://example.com/x", success=False, error="network blip"
        ),
    ):
        with pytest.raises(RuntimeError, match="network blip"):
            fetch_source("https://example.com/x", tmp_path)


def test_failed_instagram_download_hints_at_the_cookies_page(tmp_path):
    with mock.patch(
        "ofmhelpers.reel_machine.intake.download",
        return_value=DownloadResult(
            url="https://www.instagram.com/reel/abc123/",
            success=False,
            error="HTTP Error 400: Bad Request",
        ),
    ):
        with pytest.raises(RuntimeError, match="/cookies"):
            fetch_source("https://www.instagram.com/reel/abc123/", tmp_path)


def test_failed_non_instagram_download_has_no_cookie_hint(tmp_path):
    with mock.patch(
        "ofmhelpers.reel_machine.intake.download",
        return_value=DownloadResult(
            url="https://www.tiktok.com/@x/video/1", success=False, error="boom"
        ),
    ):
        with pytest.raises(RuntimeError) as exc_info:
            fetch_source("https://www.tiktok.com/@x/video/1", tmp_path)
    assert "/cookies" not in str(exc_info.value)
