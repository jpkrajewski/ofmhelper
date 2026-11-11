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

import secrets
import shutil
import subprocess
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
lazy from collections.abc import Iterator

from pydantic import BaseModel, ConfigDict

from ofmhelpers.config import settings
from ofmhelpers.integrations.downloaders import generic, images, kvs, savers
from ofmhelpers.integrations.downloaders.cookies import cookie_file
from ofmhelpers.integrations.downloaders.models import DownloadResult
from ofmhelpers.integrations.downloaders.sites import images_first
from ofmhelpers.log import get_logger
from ofmhelpers.media.video_probe import duration_s

logger = get_logger(__name__)

# Where each URL's own download directory is made under (the tools' default).
_OUTPUT_ROOT = generic.DownloadConfig().output_dir


class _Tool(BaseModel):
    """One downloader to try: its name in the reasons, and its call."""

    model_config = ConfigDict(frozen=True)

    name: str
    fetch: Callable[[], DownloadResult]


class _Download(BaseModel):
    """One URL's download into its own directory `out`, logged in as
    `cookiefile` (None: logged out). `single`: the linked post only."""

    model_config = ConfigDict(frozen=True)

    url: str
    cookiefile: str | None
    out: Path
    single: bool = False

    def video(self) -> DownloadResult:
        config = (
            generic.DownloadConfig.single_post(output_dir=self.out)
            if self.single
            else generic.DownloadConfig(output_dir=self.out)
        )
        return generic.download(self.url, config=config, cookiefile=self.cookiefile)

    def images(self) -> DownloadResult:
        most = settings.downloaders.single_post_items if self.single else None
        config = images.ImageDownloadConfig(output_dir=self.out, max_items=most)
        return images.download(self.url, config=config, cookiefile=self.cookiefile)

    def kvs(self) -> DownloadResult:
        # A KVS page is one public video: no login, nothing around it.
        return kvs.download(self.url, self.out)

    def savers(self) -> DownloadResult:
        # Saver sites log in with their own sessions.
        return savers.download(self.url, self.out, single=self.single)

    def order(self) -> list[_Tool]:
        """The tools to try, in turn. The saver sites go last: a fallback
        for posts Instagram keeps from yt-dlp and gallery-dl, never the
        first try (their sessions and rate limits are not ours to spend)."""
        video = _Tool(name="video", fetch=self.video)
        player = _Tool(name="kvs", fetch=self.kvs)
        pictures = _Tool(name="images", fetch=self.images)
        last = _Tool(name="savers", fetch=self.savers)
        if images_first(self.url):
            return [pictures, video, last]
        return [video, player, pictures, last]

    def run(self) -> DownloadResult:
        """The first tool's files that really are what they claim; when
        none gets any, every reason."""
        url = self.url
        errors: list[str] = []
        for tool in self.order():
            result = tool.fetch()
            reason = _broken_video(result) if result.success else None
            if result.success and reason is None:
                return result
            if reason is not None:
                result = DownloadResult(url=url, success=False, error=reason)
            logger.info(
                "download %s: %s gave nothing: %s", url, tool.name, result.error
            )
            errors.append(f"{tool.name}: {result.error or 'no files'}")
        # A tool that does not know the site at all says nothing useful next to
        # the other's real reason (prod, 2026-10-22: PornHub's 403 was shown as
        # gallery-dl's "Unsupported URL").
        known = [e for e in errors if "unsupported url" not in e.lower()]
        return DownloadResult(url=url, success=False, error=" | ".join(known or errors))


def _broken_video(result: DownloadResult) -> str | None:
    """Why one of `result`'s videos is not a video, deleting the broken
    files; None when every one plays."""
    broken = []
    for path in result.output_paths:
        if not generic.is_video_file(path):
            continue
        try:
            duration_s(path, settings.downloaders.probe_timeout_s)
        except OSError, RuntimeError, ValueError, subprocess.SubprocessError:
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
    return _Download(url=url, cookiefile=cookiefile, out=out).run()


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
        yield _Download(url=url, cookiefile=cookiefile, out=out, single=single).run()
    finally:
        shutil.rmtree(out, ignore_errors=True)


def download_all(urls: list[str], cookies: str | None = None) -> list[DownloadResult]:
    """Every URL, logged in with `cookies`: the downloading user's own
    cookies.txt text (`db/stores/cookies.py`), never anyone else's."""
    with cookie_file(cookies) as cookiefile:
        return [download(url, cookiefile) for url in urls]
