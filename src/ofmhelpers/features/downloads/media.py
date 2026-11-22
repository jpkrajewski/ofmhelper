from ofmhelpers.features.downloads.fetch import fetch
from ofmhelpers.integrations.downloaders.media import download_all
lazy from ofmhelpers.integrations.downloaders.models import DownloadResult

TASK = "download_media"


def run_downloads(
    urls: list[str],
    assets_root: str,
    user_id: int | None = None,
    job_id: str | None = None,
) -> list[DownloadResult]:
    return fetch(download_all, urls, assets_root, user_id, job_id)
