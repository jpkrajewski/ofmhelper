from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request

from ofmhelpers.cache import enqueue
from ofmhelpers.downloaders.images import download_all
from ofmhelpers.downloaders.models import DownloadResult
from ofmhelpers.web.middleware import AuthMiddleware
from ofmhelpers.web.routers.task_helpers import (
    add_job_routes,
    assets_root_for,
    register_grouped_results,
)
from ofmhelpers.web.schemas import JobQueued
from ofmhelpers.web.stores import cookies as login_cookies
from ofmhelpers.web.stores.jobs import create_job, run_job

router = APIRouter(prefix="/download-images", tags=["download-images"])


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
def run(request: Request, urls: Annotated[str, Form()]):
    url_list = [u.strip() for u in urls.splitlines() if u.strip()]
    if not url_list:
        raise HTTPException(status_code=400, detail="At least one URL is required")

    job_id = create_job(
        "download_images", {"urls": url_list}, actor=AuthMiddleware.actor(request)
    )
    enqueue(
        run_job,
        job_id,
        _run_downloads,
        {
            "urls": url_list,
            "assets_root": str(assets_root_for(request)),
            "user_id": AuthMiddleware.owner_id(request),
        },
    )

    return JobQueued(job_id=job_id)


add_job_routes(
    router,
    "Download images",
    lambda job: f"Downloading {len(job.params['urls'])} url(s)…",
    back_url="/download-assets",
    back_label="download more",
)
