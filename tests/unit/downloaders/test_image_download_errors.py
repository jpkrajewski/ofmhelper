"""An Instagram photo post (/p/DGUaB0Rtazx, prod 2026-10-22) shows a logged-out
visitor only its cover image: gallery-dl stops at the login redirect and
yt-dlp finds no video. The job says what to do about it instead of quoting
gallery-dl."""

import subprocess

from ofmhelpers.downloaders import images
from ofmhelpers.downloaders.images import ImageDownloadConfig

POST = "https://www.instagram.com/p/DGUaB0Rtazx/"


def _gallery_dl_says(monkeypatch, stderr: str) -> None:
    def run(cmd, **_kwargs):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr=stderr)

    monkeypatch.setattr(images.subprocess, "run", run)


def test_a_login_wall_asks_for_fresh_cookies(monkeypatch, tmp_path):
    _gallery_dl_says(
        monkeypatch,
        "[instagram][error] HTTP redirect to login page "
        "(https://www.instagram.com/accounts/login/)\n",
    )

    result = images.download(POST, ImageDownloadConfig(output_dir=tmp_path))

    assert not result.success
    assert result.error == (
        "This post needs a login: upload a fresh cookies.txt at /cookies (admin)"
    )


def test_any_other_error_is_kept_as_gallery_dl_said_it(monkeypatch, tmp_path):
    _gallery_dl_says(monkeypatch, "[instagram][error] 404 Not Found\n")

    result = images.download(POST, ImageDownloadConfig(output_dir=tmp_path))

    assert result.error == "[instagram][error] 404 Not Found"


def test_no_error_and_no_files_says_so(monkeypatch, tmp_path):
    _gallery_dl_says(monkeypatch, "")

    result = images.download(POST, ImageDownloadConfig(output_dir=tmp_path))

    assert result.error == "gallery-dl finished but no files were written"
