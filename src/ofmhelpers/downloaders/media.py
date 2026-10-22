"""Any link, whatever it holds: a video, a photo post, a carousel.

The person pasting links should not have to know which tool a post needs.
Each URL goes to both downloaders in turn and keeps the first that gets
files: yt-dlp (`generic.py`, videos) or gallery-dl (`images.py`, photos and
mixed posts). Which one goes first is decided by the link: kinds of posts
that are usually photos or carousels (`_IMAGES_FIRST`) start with
gallery-dl, everything else with yt-dlp. A new site = one entry there.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ofmhelpers.downloaders import generic, images
from ofmhelpers.downloaders.cookies import cookie_file
from ofmhelpers.downloaders.models import DownloadResult
from ofmhelpers.log import get_logger

if TYPE_CHECKING:
    from collections.abc import Callable

logger = get_logger(__name__)

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


def _video(url: str, cookiefile: str | None) -> DownloadResult:
    return generic.download(url, cookiefile=cookiefile)


def _images(url: str, cookiefile: str | None) -> DownloadResult:
    return images.download(url, cookiefile=cookiefile)


def _order(
    url: str,
) -> list[tuple[str, Callable[[str, str | None], DownloadResult]]]:
    video, pictures = ("video", _video), ("images", _images)
    return [pictures, video] if _IMAGES_FIRST.search(url) else [video, pictures]


def download(url: str, cookiefile: str | None = None) -> DownloadResult:
    """The first downloader's files, logged in as `cookiefile` (None:
    logged out); when none gets any, both reasons."""
    errors: list[str] = []
    for name, fetch in _order(url):
        result = fetch(url, cookiefile)
        if result.success:
            return result
        logger.info("download %s: %s gave nothing: %s", url, name, result.error)
        errors.append(f"{name}: {result.error or 'no files'}")
    return DownloadResult(url=url, success=False, error=" | ".join(errors))


def download_all(urls: list[str], cookies: str | None = None) -> list[DownloadResult]:
    """Every URL, logged in with `cookies`: the downloading user's own
    cookies.txt text (`web/stores/cookies.py`), never anyone else's."""
    with cookie_file(cookies) as cookiefile:
        return [download(url, cookiefile) for url in urls]
