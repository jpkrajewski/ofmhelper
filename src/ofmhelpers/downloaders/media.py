"""Any link, whatever it holds: a video, a photo post, a carousel.

The person pasting links should not have to know which tool a post needs.
Each URL goes to the downloaders in turn and keeps the first that gets
files that really are what they claim: yt-dlp (`generic.py`, videos), the
KVS player reader (`kvs.py`, tube sites yt-dlp gets wrong) and gallery-dl
(`images.py`, photos and mixed posts). Which goes first is decided by the
link: kinds of posts that are usually photos or carousels (`_IMAGES_FIRST`)
start with gallery-dl, everything else with yt-dlp. A new site = one entry
there; a new tool = one entry in `_order`.

A "video" that ffprobe cannot read (a site's web page saved as .mp4,
2026-10-23) is deleted and counts as that tool failing, so the next one
gets its turn -- never a broken file reported as downloaded.
"""

from __future__ import annotations

import re
import secrets
import subprocess
from typing import TYPE_CHECKING

from ofmhelpers.config import settings
from ofmhelpers.downloaders import generic, images, kvs
from ofmhelpers.downloaders.cookies import cookie_file
from ofmhelpers.downloaders.models import DownloadResult
from ofmhelpers.log import get_logger
from ofmhelpers.utils.video_probe import duration_s

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

logger = get_logger(__name__)

# Where each URL's own download directory is made under (the tools' default).
_OUTPUT_ROOT = generic.DownloadConfig().output_dir

# Links whose posts are mostly photos or carousels: an Instagram post (a
# reel is /reel/), an X/Twitter status, a Pinterest pin, a TikTok photo post.
# gallery-dl gets every item of such a post, yt-dlp only its videos.
_IMAGES_FIRST = re.compile(
    r"instagram\.com/(?:[^/]+/)?p/"
    r"|(?:^|[/.])(?:x|twitter)\.com/"
    r"|pinterest\.|pin\.it/"
    r"|tiktok\.com/.*/photo/",
    re.IGNORECASE,
)


def _video(url: str, cookiefile: str | None, out: Path) -> DownloadResult:
    return generic.download(
        url, config=generic.DownloadConfig(output_dir=out), cookiefile=cookiefile
    )


def _images(url: str, cookiefile: str | None, out: Path) -> DownloadResult:
    return images.download(
        url, config=images.ImageDownloadConfig(output_dir=out), cookiefile=cookiefile
    )


def _kvs(url: str, cookiefile: str | None, out: Path) -> DownloadResult:
    del cookiefile  # a KVS video is public
    return kvs.download(url, out)


def _order(
    url: str,
) -> list[tuple[str, Callable[[str, str | None, Path], DownloadResult]]]:
    video, player, pictures = ("video", _video), ("kvs", _kvs), ("images", _images)
    if _IMAGES_FIRST.search(url):
        return [pictures, video]
    return [video, player, pictures]


def _broken_video(result: DownloadResult) -> str | None:
    """Why one of `result`'s videos is not a video, deleting the broken
    files; None when every one plays."""
    broken = []
    for path in result.output_paths:
        if not generic.is_video_file(path):
            continue
        try:
            duration_s(path, settings.downloaders.probe_timeout_s)
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
            logger.warning("download %s: %s is not a video", result.url, path.name)
            path.unlink(missing_ok=True)
            broken.append(path.name)
    if not broken:
        return None
    return "the site sent a web page, not the video"


def download(url: str, cookiefile: str | None = None) -> DownloadResult:
    """The first downloader's files, logged in as `cookiefile` (None:
    logged out); when none gets any, both reasons.

    Each URL downloads into its own fresh directory: the tools name files
    from the post (and some extractors have no unique id -- every titleless
    X video is `twitter_None_1.mp4`), so a shared folder let one download
    overwrite another's file (prod, 2026-10-23). Isolated, no two collide."""
    out = _OUTPUT_ROOT / secrets.token_hex(8)
    errors: list[str] = []
    for name, fetch in _order(url):
        result = fetch(url, cookiefile, out)
        reason = _broken_video(result) if result.success else None
        if result.success and reason is None:
            return result
        if reason is not None:
            result = DownloadResult(url=url, success=False, error=reason)
        logger.info("download %s: %s gave nothing: %s", url, name, result.error)
        errors.append(f"{name}: {result.error or 'no files'}")
    # A tool that does not know the site at all says nothing useful next to
    # the other's real reason (prod, 2026-10-22: PornHub's 403 was shown as
    # gallery-dl's "Unsupported URL").
    known = [e for e in errors if "unsupported url" not in e.lower()]
    return DownloadResult(url=url, success=False, error=" | ".join(known or errors))


def download_all(urls: list[str], cookies: str | None = None) -> list[DownloadResult]:
    """Every URL, logged in with `cookies`: the downloading user's own
    cookies.txt text (`web/stores/cookies.py`), never anyone else's."""
    with cookie_file(cookies) as cookiefile:
        return [download(url, cookiefile) for url in urls]
