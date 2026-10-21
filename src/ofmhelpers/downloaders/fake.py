"""yt-dlp and gallery-dl stand-ins (OFM_FAKE_EXTERNALS): a URL "downloads"
to a real local file -- a playable mp4, or two images for a post -- named
from the URL, so the pages, jobs and file serving run unchanged. A URL
containing "fake-fail" fails the way a removed post does."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from ofmhelpers.downloaders.models import DownloadResult
from ofmhelpers.utils import fake_media

if TYPE_CHECKING:
    from pathlib import Path

_FAIL = "fake-fail"


def _stem(url: str) -> str:
    return "fake-" + hashlib.sha256(url.encode()).hexdigest()[:10]


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
