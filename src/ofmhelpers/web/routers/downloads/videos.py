from typing import Annotated

from fastapi import APIRouter, Form

from ofmhelpers.features.downloads import videos

# Jobs queued before 2026-11-11 name the run function by this module's path.
from ofmhelpers.features.downloads.videos import run_downloads as _run_downloads
from ofmhelpers.schemas.access import Area
from ofmhelpers.schemas.downloads import DownloadsQueued
from ofmhelpers.web.dependencies import Access, Actor, AssetsRoot, OwnerId
from ofmhelpers.web.routers.task_helpers import add_job_routes

__all__ = ["_run_downloads", "router"]

router = APIRouter(
    prefix="/download-videos",
    tags=["download-videos"],
    dependencies=[Access(Area.DOWNLOAD_OLD)],
)


@router.post("/run", status_code=202)
def run(
    urls: Annotated[str, Form()],
    actor: Actor,
    owner_id: OwnerId,
    assets_root: AssetsRoot,
) -> DownloadsQueued:
    return videos.start(urls, actor, owner_id, assets_root)


add_job_routes(
    router,
    "Download videos",
    lambda job: f"Downloading {len(job.params['urls'])} url(s)…",
    back_url="/download-assets",
    back_label="download more",
)
