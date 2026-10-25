"""yt-dlp and gallery-dl stand-ins (OFM_FAKE_EXTERNALS): a URL "downloads"
to a real local file -- a playable mp4, or two images for a post -- named
from the URL, so the pages, jobs and file serving run unchanged. A URL
containing "fake-fail" fails the way a removed post does.

The look at a link (`info`, `generic.video_info`) agrees with the download:
a photo-post link (`sites.IMAGES_FIRST`, gallery-dl first: two jpgs) has no
video. Markers steer it for tests: "fake-long" = a 20 s video,
"fake-login" = the site wants a login, "fake-thumb" = a cover URL the fake
Instagram CDN serves (`instagram/fake.py`, through `utils/outbound.py`).
The download of a "fake-long" link is still the 2 s clip."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from ofmhelpers.downloaders.models import DownloadResult, VideoInfo
from ofmhelpers.downloaders.sites import images_first
from ofmhelpers.utils import fake_media

if TYPE_CHECKING:
    from pathlib import Path

_FAIL = "fake-fail"
_LONG = "fake-long"
_LOGIN = "fake-login"
_THUMB = "fake-thumb"
LONG_SECONDS = 20.0
NO_VIDEO = "No video at this link (a photo post?)"
LOGIN_REQUIRED = (
    "ERROR: [Instagram] fake-login: login required. "
    "Use --cookies-from-browser or --cookies for the authentication"
)


def _stem(url: str) -> str:
    return "fake-" + hashlib.sha256(url.encode()).hexdigest()[:10]


def info(url: str) -> VideoInfo:
    """What a look at the link says: the 2 s fake reel (no cover), unless a
    marker or a photo-post link says otherwise (module docstring)."""
    if _FAIL in url:
        return VideoInfo(url=url, error="ERROR: Video unavailable")
    if _LOGIN in url:
        return VideoInfo(url=url, error=LOGIN_REQUIRED)
    if images_first(url):
        return VideoInfo(url=url, error=NO_VIDEO)
    return VideoInfo(
        url=url,
        seconds=LONG_SECONDS if _LONG in url else float(fake_media.MP4_SECONDS),
        thumbnail=_thumbnail(url) if _THUMB in url else None,
    )


def _thumbnail(url: str) -> str:
    # Deferred: the instagram package imports downloaders.cookies.
    from ofmhelpers.instagram.fake import CDN

    return f"{CDN}/{_stem(url)}.jpg"


def video(url: str, output_dir: Path) -> DownloadResult:
    if _FAIL in url:
        return DownloadResult(url=url, success=False, error="ERROR: Video unavailable")
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{_stem(url)}.mp4"
    path.write_bytes(fake_media.mp4())
    return DownloadResult(url=url, success=True, output_paths=[path])


def images(url: str, output_dir: Path) -> DownloadResult:
    if _FAIL in url:
        return DownloadResult(url=url, success=False, error="gallery-dl: 404 Not Found")
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for i, color in enumerate(("salmon", "steelblue"), start=1):
        path = output_dir / f"{_stem(url)}_{i}.jpg"
        path.write_bytes(fake_media.image(color))
        paths.append(path)
    return DownloadResult(url=url, success=True, output_paths=paths)
