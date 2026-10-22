"""The three GET endpoints every background-job tool exposes, registered once.

A tool router is then its own page and `/run`, plus one `add_job_routes` call:
the status page, its polling payload and the result download all live here,
under the router's own prefix.
"""

from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request

from ofmhelpers.web.middleware import AuthMiddleware
from ofmhelpers.web.routers.task_helpers.responses import (
    job_assets,
    job_inputs,
    job_status_payload,
)
from ofmhelpers.web.routers.task_helpers.serving import (
    serve_job_file,
    serve_job_poster,
    serve_job_preview,
)
from ofmhelpers.web.schemas import Job
from ofmhelpers.web.stores.jobs import get_job
from ofmhelpers.web.templates_config import render


def add_job_routes(
    router: APIRouter,
    title: str,
    pending_message: Callable[[Job], str],
    back_url: str,
    back_label: str,
    show_inputs: bool = False,
) -> None:
    """`pending_message` words the status page of a still-running job (it may
    read the job's params). `show_inputs` adds the job's own settings and
    reference files to it -- the generation tools, whose params are those."""
    files_prefix = f"{router.prefix}/files"

    @router.get("/jobs/{job_id}")
    def job_status(request: Request, job_id: str):
        job = get_job(job_id, AuthMiddleware.actor(request))
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        assets, failed_sources = job_assets(job, files_prefix)
        return render(
            request,
            "job_status.html",
            job=job,
            assets=assets,
            failed_sources=failed_sources,
            job_inputs=job_inputs(job) if show_inputs else None,
            title=title,
            pending_message=pending_message(job),
            back_url=back_url,
            back_label=back_label,
        )

    @router.get("/jobs/{job_id}/status")
    def job_status_json(request: Request, job_id: str):
        job = get_job(job_id, AuthMiddleware.actor(request))
        return job_status_payload(job, files_prefix)

    @router.get("/files/{job_id}/{index}")
    def download_file(
        request: Request, job_id: str, index: int, dl: Annotated[int, Query()] = 0
    ):
        job = get_job(job_id, AuthMiddleware.actor(request))
        return serve_job_file(job, index, as_attachment=bool(dl))

    @router.get("/files/{job_id}/{index}/preview")
    def preview_file(request: Request, job_id: str, index: int):
        job = get_job(job_id, AuthMiddleware.actor(request))
        return serve_job_preview(job, index)

    @router.get("/files/{job_id}/{index}/poster")
    def poster_file(request: Request, job_id: str, index: int):
        job = get_job(job_id, AuthMiddleware.actor(request))
        return serve_job_poster(job, index)
