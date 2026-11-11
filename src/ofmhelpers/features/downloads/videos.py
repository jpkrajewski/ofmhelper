"""The old Download videos tool (yt-dlp for every link): its route and its
past jobs."""

from pathlib import Path

from ofmhelpers.db.stores import cookies as login_cookies
from ofmhelpers.features.assets.store import register_grouped_results
from ofmhelpers.features.downloads.service import queue_downloads
from ofmhelpers.integrations.downloaders.generic import download_all
lazy from ofmhelpers.integrations.downloaders.models import DownloadResult
lazy from ofmhelpers.schemas import JobQueued

TASK = "download_videos"


def run_downloads(
    urls: list[str], assets_root: str, user_id: int | None = None
) -> list[DownloadResult]:
    """Runs in the background, logged in as the user who started it (their
    own cookies.txt, read here: never in the queued job); run_job stores
    the results as JSON."""
    results = download_all(urls, cookies=login_cookies.for_user(user_id))
    register_grouped_results(results, Path(assets_root))
    return results


def start(pasted: str, actor: str, owner_id: int, assets_root: Path) -> JobQueued:
    return queue_downloads(TASK, pasted, run_downloads, actor, owner_id, assets_root)
