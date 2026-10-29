"""Any link, whatever it holds: a video, a photo post, a carousel.

The person pasting links should not have to know which tool a post needs.
Each URL goes to the downloaders in turn and keeps the first that gets
files that really are what they claim: yt-dlp (`generic.py`, videos), the
KVS player reader (`kvs.py`, tube sites yt-dlp gets wrong), gallery-dl
(`images.py`, photos and mixed posts) and, for Instagram posts only,
the saver sites (`savers.py`) -- the fallback for posts Instagram hides
from the logged-out API. Which goes first is decided by the link: kinds
of posts that are usually photos or carousels (`sites.IMAGES_FIRST`)
start with gallery-dl, everything else with yt-dlp. A new site = one
entry there; a new tool = one entry in `_order`.

A "video" that ffprobe cannot read (a site's web page saved as .mp4,
2026-10-23) is deleted and counts as that tool failing, so the next one
gets its turn -- never a broken file reported as downloaded.
"""

from __future__ import annotations

import secrets
import shutil
import subprocess
from contextlib import contextmanager
from typing import TYPE_CHECKING

from ofmhelpers.config import settings
from ofmhelpers.downloaders import generic, images, kvs, savers
from ofmhelpers.downloaders.cookies import cookie_file
from ofmhelpers.downloaders.models import DownloadResult
from ofmhelpers.downloaders.sites import images_first
from ofmhelpers.log import get_logger
from ofmhelpers.utils.video_probe import duration_s

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

logger = get_logger(__name__)

# Where each URL's own download directory is made under (the tools' default).
_OUTPUT_ROOT = generic.DownloadConfig().output_dir


def _video(url: str, cookiefile: str | None, out: Path, single: bool) -> DownloadResult:
    config = (
        generic.DownloadConfig.single_post(output_dir=out)
        if single
        else generic.DownloadConfig(output_dir=out)
    )
    return generic.download(url, config=config, cookiefile=cookiefile)


def _images(
    url: str, cookiefile: str | None, out: Path, single: bool
) -> DownloadResult:
    most = settings.downloaders.single_post_items if single else None
    config = images.ImageDownloadConfig(output_dir=out, max_items=most)
    return images.download(url, config=config, cookiefile=cookiefile)


def _kvs(url: str, cookiefile: str | None, out: Path, single: bool) -> DownloadResult:
    del cookiefile, single  # a KVS page is one public video
    return kvs.download(url, out)


def _order(
    url: str,
) -> list[tuple[str, Callable[[str, str | None, Path, bool], DownloadResult]]]:
    """The tools to try, in turn; each takes (url, cookiefile, its
    directory, one post only). The saver sites go last: a fallback for
    posts Instagram keeps from yt-dlp and gallery-dl, never the first
    try (their sessions and rate limits are not ours to spend)."""
    video, player, pictures = ("video", _video), ("kvs", _kvs), ("images", _images)
    if images_first(url):
        return [pictures, video, ("savers", _savers)]
    return [video, player, pictures, ("savers", _savers)]


def _savers(
    url: str, cookiefile: str | None, out: Path, single: bool
) -> DownloadResult:
    del cookiefile  # saver sites log in with their own sessions
    return savers.download(url, out, single=single)


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
    return _download_into(url, cookiefile, _OUTPUT_ROOT / secrets.token_hex(8))


@contextmanager
def downloaded(
    url: str, cookiefile: str | None = None, *, single: bool = False
) -> Iterator[DownloadResult]:
    """`download`, for a caller that only needs the files while the block
    runs (it copies what it keeps, e.g. into the asset store): the URL's own
    directory, partial files included, is removed on the way out, whatever
    happened. `single`: the linked post only, never a playlist, profile or
    story tray around it (/clone-links clones one video per link): yt-dlp
    gets `DownloadConfig.single_post`, gallery-dl the same entry limit
    (`--range`)."""
    out = _OUTPUT_ROOT / secrets.token_hex(8)
    try:
        yield _download_into(url, cookiefile, out, single=single)
    finally:
        shutil.rmtree(out, ignore_errors=True)


def _download_into(
    url: str, cookiefile: str | None, out: Path, *, single: bool = False
) -> DownloadResult:
    errors: list[str] = []
    for name, fetch in _order(url):
        result = fetch(url, cookiefile, out, single)
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
