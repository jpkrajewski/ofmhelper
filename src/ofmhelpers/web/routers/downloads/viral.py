"""
/viral: one Instagram profile in, its most played reels out. A search is a
job (task `viral_reels`): on the worker Apify reads the profile's newest
`OFM_VIRAL_SCAN` reels (`ofmhelpers.features.viral`), the `OFM_VIRAL_TOP` (30) most
played are downloaded, and the page shows them most played first, each with
Download, all of them as one zip. The page shows the search picked
(`?job=`), else the newest, and lists the earlier ones
(`features/viral/page.py`).
"""

import shutil
from typing import Annotated

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import FileResponse, Response
from starlette.background import BackgroundTask

from ofmhelpers.auth.access import Area
from ofmhelpers.features.viral import page as viral_page
from ofmhelpers.features.viral.page import run_viral
from ofmhelpers.schemas import JobQueued
from ofmhelpers.web.dependencies import Access, Actor
from ofmhelpers.web.routers.task_helpers import (
    add_job_routes,
    uploads_root,
)
from ofmhelpers.web.templates_config import render

TASK = viral_page.TASK
LABEL = "Viral"

router = APIRouter(prefix="/viral", tags=["viral"], dependencies=[Access(Area.VIRAL)])

_FILES = f"{router.prefix}/files"

JobQuery = Annotated[str | None, Query(max_length=32)]


@router.get("")
def page(request: Request, actor: Actor, job: JobQuery = None) -> Response:
    view = viral_page.page_view(actor, job, _FILES)
    return render(request, "viral.html", **dict(view))


@router.post("/run", status_code=202)
def run(actor: Actor, profile: Annotated[str, Form()] = "") -> JobQueued:
    return viral_page.start(profile, actor, run_viral)


@router.get("/zip/{job_id}")
def download_all(job_id: str, actor: Actor) -> FileResponse:
    """Every reel of a finished search as one zip, named after the profile."""
    made = viral_page.archive(job_id, actor, uploads_root() / "viral-zips")
    return FileResponse(
        made.path,
        media_type="application/zip",
        filename=made.path.name,
        background=BackgroundTask(shutil.rmtree, made.folder, ignore_errors=True),
    )


add_job_routes(
    router,
    LABEL,
    lambda job: f"Finding @{job.params.get('profile', '')}'s viral reels…",
    back_url="/viral",
    back_label="search another profile",
)
