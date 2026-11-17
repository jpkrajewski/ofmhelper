"""/download-media: any links -- videos, photo posts, carousels -- in one go.
The downloader picks yt-dlp or gallery-dl per link
(`ofmhelpers.integrations.downloaders.media`), so nobody has to choose. A
card's video goes on to /clone or /clone-long through `features/assets/send.py`."""

from pathlib import Path

from ofmhelpers.features.downloads.service import fetch, queue_downloads
from ofmhelpers.integrations.downloaders.media import download_all
lazy from ofmhelpers.integrations.downloaders.models import DownloadResult
lazy from ofmhelpers.schemas.downloads import DownloadsQueued

TASK = "download_media"


def run_downloads(
    urls: list[str],
    assets_root: str,
    user_id: int | None = None,
    job_id: str | None = None,
) -> list[DownloadResult]:
    """Runs in the background, logged in as the user who started it;
    run_job stores the results as JSON. Run again, it fetches only what is
    not on disk (`service.fetch`)."""
    return fetch(download_all, urls, assets_root, user_id, job_id)


def start(pasted: str, actor: str, owner_id: int, assets_root: Path) -> DownloadsQueued:
    return queue_downloads(TASK, pasted, run_downloads, actor, owner_id, assets_root)
