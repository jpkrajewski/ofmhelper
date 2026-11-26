# Copyright (C) 2026 jpkrajewski

from typing import Annotated

from fastapi import APIRouter, Form

from ofmhelpers.db.downloads.schema import DownloadsQueued
from ofmhelpers.features.downloads.videos import run_downloads as _run_downloads
from ofmhelpers.web.access import Area
from ofmhelpers.web.dependencies import Access, Downloads
from ofmhelpers.web.routers.task_helpers import add_job_routes

__all__ = ["_run_downloads", "router"]

router = APIRouter(
    prefix="/download-videos",
    tags=["download-videos"],
    dependencies=[Access(Area.DOWNLOAD_OLD)],
)


@router.post("/run", status_code=202)
def run(urls: Annotated[str, Form()], downloads: Downloads) -> DownloadsQueued:
    return downloads.videos.start(urls)


add_job_routes(
    router,
    "Download videos",
    lambda job: f"Downloading {len(job.params['urls'])} url(s)…",
    back_url="/download-assets",
    back_label="download more",
)
