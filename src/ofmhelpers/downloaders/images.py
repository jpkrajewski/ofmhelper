from __future__ import annotations

import subprocess
import time
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from ofmhelpers.config import settings
from ofmhelpers.downloaders import fake
from ofmhelpers.downloaders.cookies import cookie_file
from ofmhelpers.downloaders.models import DownloadResult

# gallery-dl errors that mean "this site wants a login", as the message the
# person downloading can act on. Instagram shows a logged-out visitor only a
# post's cover image, so gallery-dl gets nothing (verified 2026-10-22, also
# through OFM_IG_PROXIES). A new site = one entry.
_LOGIN_WALLS = {
    "redirect to login page": (
        "This post needs a login: upload your fresh cookies.txt at /cookies"
    ),
}


def _error(stderr: str) -> str:
    """gallery-dl's stderr as the job's error: a known login wall in plain
    words, anything else as gallery-dl said it."""
    for marker, message in _LOGIN_WALLS.items():
        if marker in stderr:
            return message
    return stderr.strip() or "gallery-dl finished but no files were written"


class ImageDownloadConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    output_dir: Path = Path("downloads")
    # {id} doesn't exist for every extractor and silently becomes "None",
    # causing filename collisions across different posts (gallery-dl then
    # skips "already downloaded" files). post_shortcode is Instagram's real
    # unique identifier; num disambiguates multiple images in one post.
    filename_template: str = "{category}_{post_shortcode}_{num}.{extension}"
    extra_args: list[str] = []

    def gallery_dl_cmd(self, url: str, cookiefile: str | None = None) -> list[str]:
        cmd = [
            "gallery-dl",
            "-D",
            str(self.output_dir),
            "-f",
            self.filename_template,
            "--no-mtime",
            "--no-skip",  # overwrite existing files instead of silently skipping them
        ]

        if cookiefile:
            cmd += ["--cookies", cookiefile]

        cmd += self.extra_args
        cmd.append(url)
        return cmd


def download(
    url: str, config: ImageDownloadConfig | None = None, cookiefile: str | None = None
) -> DownloadResult:
    """`url`'s files, logged in as `cookiefile` (None: logged out)."""
    config = config or ImageDownloadConfig()
    if settings.infra.fake_externals:
        return fake.images(url, config.output_dir)
    config.output_dir.mkdir(parents=True, exist_ok=True)

    before_mtimes = {
        p: p.stat().st_mtime for p in config.output_dir.rglob("*") if p.is_file()
    }
    start_time = time.time()

    cmd = config.gallery_dl_cmd(url, cookiefile)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=settings.downloaders.image_download_timeout_s,
        )
    except subprocess.TimeoutExpired:
        return DownloadResult(url=url, success=False, error="Download timed out")
    except FileNotFoundError:
        return DownloadResult(
            url=url,
            success=False,
            error="gallery-dl not installed. Run: pip install gallery-dl",
        )

    changed_files = []
    for p in config.output_dir.rglob("*"):
        if not p.is_file():
            continue
        mtime = p.stat().st_mtime
        # new file, or existing file that was just overwritten during this run
        if (p not in before_mtimes or mtime > before_mtimes[p]) and (
            mtime >= start_time - 1
        ):  # small buffer for filesystem timestamp granularity
            changed_files.append(p)

    changed_files.sort(key=lambda p: p.stat().st_mtime)

    if not changed_files:
        return DownloadResult(url=url, success=False, error=_error(result.stderr))

    return DownloadResult(url=url, success=True, output_paths=changed_files)


def download_all(
    urls: list[str],
    config: ImageDownloadConfig | None = None,
    cookies: str | None = None,
) -> list[DownloadResult]:
    """Every URL, logged in with `cookies` (the user's cookies.txt text)."""
    config = config or ImageDownloadConfig()
    with cookie_file(cookies) as cookiefile:
        return [download(url, config, cookiefile) for url in urls]
