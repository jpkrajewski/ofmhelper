"""
/viral: one Instagram profile in, its most played reels out. A search is a
job (task `viral_reels`): on the worker Apify reads the profile's newest
`OFM_VIRAL_SCAN` reels (`ofmhelpers.viral`), the `OFM_VIRAL_TOP` (30) most
played are downloaded, and the page shows them most played first, each with
Download, all of them as one zip. The page shows the search picked
(`?job=`), else the newest, and lists the earlier ones.
"""

import shutil
import zipfile
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import ValidationError
from starlette.background import BackgroundTask

from ofmhelpers.cache import enqueue
from ofmhelpers.config import settings
from ofmhelpers.instagram import ProfileHandle
from ofmhelpers.viral import find_viral
from ofmhelpers.web.middleware import AuthMiddleware
from ofmhelpers.web.routers.task_helpers import (
    UPLOADS_ROOT,
    add_job_routes,
    job_assets,
    make_job_dir,
)
from ofmhelpers.web.schemas import Job, JobQueued, ViralFile, ViralSearch
from ofmhelpers.web.stores.jobs import (
    create_job,
    get_job,
    list_jobs_page,
    merge_params,
    run_job,
)
from ofmhelpers.web.templates_config import render

TASK = "viral_reels"
LABEL = "Viral"

router = APIRouter(prefix="/viral", tags=["viral"])

_FILES = f"{router.prefix}/files"
# The earlier searches the page lists.
_HISTORY = 20

JobQuery = Annotated[str | None, Query(max_length=32)]


def run_viral(job_id: str, profile: str) -> list[ViralFile]:
    """The worker's half: the profile's top reels saved in a fresh folder;
    what Apify read and charged goes on the job's params."""
    found = find_viral(profile, make_job_dir(UPLOADS_ROOT / "viral"))
    merge_params(job_id, {"scanned": found.scanned, "cost_usd": found.cost_usd})
    return [
        ViralFile(
            name=r.path.name,
            path=str(r.path),
            code=r.reel.code,
            url=r.reel.url,
            plays=r.reel.plays,
            likes=r.reel.likes,
            comments=r.reel.comments,
            posted_at=r.reel.posted_at,
            seconds=r.reel.seconds,
        )
        for r in found.reels
    ]


def _search(job: Job) -> ViralSearch:
    files, _ = job_assets(job, _FILES)
    return ViralSearch.of(job, files)


@router.get("")
def page(request: Request, job: JobQuery = None):
    actor = AuthMiddleware.actor(request)
    searches, _ = list_jobs_page({TASK}, 0, _HISTORY, actor)
    picked = get_job(job, actor) if job else None
    current = picked if picked and picked.task == TASK else None
    if current is None and searches and not job:
        current = searches[0]
    return render(
        request,
        "viral.html",
        current=_search(current) if current else None,
        searches=[ViralSearch.of(j, []) for j in searches],
        top=settings.viral.top,
        scan=settings.viral.scan,
        max_usd=settings.viral.max_usd,
    )


@router.post("/run")
def run(request: Request, profile: Annotated[str, Form()] = "") -> JobQueued:
    try:
        handle = ProfileHandle(username=profile).username
    except ValidationError as exc:
        raise HTTPException(
            status_code=400, detail="Paste an Instagram profile: @name or its link"
        ) from exc
    job_id = create_job(TASK, {"profile": handle}, actor=AuthMiddleware.actor(request))
    enqueue(run_job, job_id, run_viral, {"job_id": job_id, "profile": handle})
    return JobQueued(job_id=job_id)


@router.get("/zip/{job_id}")
def download_all(request: Request, job_id: str) -> FileResponse:
    """Every reel of a finished search as one zip (stored: H.264 does not
    compress further), named after the profile."""
    job = get_job(job_id, AuthMiddleware.actor(request))
    if job is None or job.task != TASK:
        raise HTTPException(status_code=404, detail="No such search")
    files = [Path(p) for p in job.local_paths() if p and Path(p).is_file()]
    if not files:
        raise HTTPException(status_code=404, detail="This search has no reels")
    profile = job.params.get("profile", "reels")
    folder = make_job_dir(UPLOADS_ROOT / "viral-zips")
    archive = folder / f"{profile}-top-{len(files)}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_STORED) as zf:
        for rank, file in enumerate(files, start=1):
            zf.write(file, f"{rank:02d}-{file.name}")
    return FileResponse(
        archive,
        media_type="application/zip",
        filename=archive.name,
        background=BackgroundTask(shutil.rmtree, folder, ignore_errors=True),
    )


add_job_routes(
    router,
    LABEL,
    lambda job: f"Finding @{job.params.get('profile', '')}'s viral reels…",
    back_url="/viral",
    back_label="search another profile",
)
