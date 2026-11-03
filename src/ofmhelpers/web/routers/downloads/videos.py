from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Form

from ofmhelpers.db.stores import cookies as login_cookies
from ofmhelpers.integrations.downloaders.generic import download_all
from ofmhelpers.integrations.downloaders.models import DownloadResult
from ofmhelpers.schemas import JobQueued
from ofmhelpers.web.dependencies import Actor, AssetsRoot, OwnerId
from ofmhelpers.web.routers.downloads.shared import queue_downloads
from ofmhelpers.web.routers.task_helpers import add_job_routes, register_grouped_results

router = APIRouter(prefix="/download-videos", tags=["download-videos"])


def _run_downloads(
    urls: list[str], assets_root: str, user_id: int | None = None
) -> list[DownloadResult]:
    """Runs in the background, logged in as the user who started it (their
    own cookies.txt, read here: never in the queued job); run_job stores
    the results as JSON."""
    results = download_all(urls, cookies=login_cookies.for_user(user_id))
    register_grouped_results(results, Path(assets_root))
    return results


@router.post("/run")
def run(
    urls: Annotated[str, Form()],
    actor: Actor,
    owner_id: OwnerId,
    assets_root: AssetsRoot,
) -> JobQueued:
    return queue_downloads(
        "download_videos", urls, _run_downloads, actor, owner_id, assets_root
    )


add_job_routes(
    router,
    "Download videos",
    lambda job: f"Downloading {len(job.params['urls'])} url(s)…",
    back_url="/download-assets",
    back_label="download more",
)
