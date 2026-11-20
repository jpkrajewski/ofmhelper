lazy from pathlib import Path

from ofmhelpers.features.downloads.service import fetch, queue_downloads
from ofmhelpers.integrations.downloaders.generic import download_all
lazy from ofmhelpers.db.downloads.schema import DownloadsQueued
lazy from ofmhelpers.integrations.downloaders.models import DownloadResult

TASK = "download_videos"


def run_downloads(
    urls: list[str],
    assets_root: str,
    user_id: int | None = None,
    job_id: str | None = None,
) -> list[DownloadResult]:
    return fetch(download_all, urls, assets_root, user_id, job_id)


def start(pasted: str, actor: str, owner_id: int, assets_root: Path) -> DownloadsQueued:
    return queue_downloads(TASK, pasted, run_downloads, actor, owner_id, assets_root)
