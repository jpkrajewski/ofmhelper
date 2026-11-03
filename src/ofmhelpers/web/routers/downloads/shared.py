"""What the download routes share: a pasted box becomes one queued job."""

from collections.abc import Callable
from typing import Any

from fastapi import HTTPException, Request

from ofmhelpers.cache import enqueue
from ofmhelpers.db.stores.jobs import create_job, run_job
from ofmhelpers.integrations.downloaders.urls import NotALinkError, read_links
from ofmhelpers.schemas import JobQueued
from ofmhelpers.web.middleware import AuthMiddleware
from ofmhelpers.web.routers.task_helpers import assets_root_for


def queue_downloads(
    request: Request, task: str, pasted: str, run: Callable[..., Any]
) -> JobQueued:
    """One job of `task` for the links pasted in the box, run by `run`
    (`_run_downloads` of the route's own module: RQ stores that path)."""
    try:
        links = read_links(pasted)
    except NotALinkError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    if not links:
        raise HTTPException(status_code=400, detail="At least one URL is required")
    job_id = create_job(task, {"urls": links}, actor=AuthMiddleware.actor(request))
    enqueue(
        run_job,
        job_id,
        run,
        {
            "urls": links,
            "assets_root": str(assets_root_for(request)),
            "user_id": AuthMiddleware.owner_id(request),
        },
    )
    return JobQueued(job_id=job_id)
