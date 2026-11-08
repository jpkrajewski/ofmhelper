"""The five GET endpoints every background-job tool exposes, registered once.

A tool router is then its own page and `/run`, plus one `add_job_routes` call:
the status page, its polling payload, the result download and the result's
preview and poster all live here, under the router's own prefix.
"""

from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response

from ofmhelpers.db.stores.jobs import get_job
from ofmhelpers.schemas import Job, JobStatusPayload
from ofmhelpers.web.dependencies import Actor
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
from ofmhelpers.web.templates_config import render


def _pending_text(pending_message: Callable[[Job], str], job: Job) -> str:
    """The running job's wording; a plain one when its stored params are not
    what the wording reads (a job of an older release)."""
    try:
        return pending_message(job)
    except KeyError:
        return "Working…"


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
    def job_status(request: Request, job_id: str, actor: Actor) -> Response:
        job = get_job(job_id, actor)
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
            pending_message=_pending_text(pending_message, job),
            back_url=back_url,
            back_label=back_label,
        )

    @router.get("/jobs/{job_id}/status")
    def job_status_json(job_id: str, actor: Actor) -> JobStatusPayload:
        return job_status_payload(get_job(job_id, actor), files_prefix)

    @router.get("/files/{job_id}/{index}")
    def download_file(
        job_id: str, index: int, actor: Actor, dl: Annotated[int, Query()] = 0
    ) -> Response:
        return serve_job_file(get_job(job_id, actor), index, as_attachment=bool(dl))

    @router.get("/files/{job_id}/{index}/preview")
    def preview_file(job_id: str, index: int, actor: Actor) -> Response:
        return serve_job_preview(get_job(job_id, actor), index, files_prefix)

    @router.get("/files/{job_id}/{index}/poster")
    def poster_file(job_id: str, index: int, actor: Actor) -> Response:
        return serve_job_poster(get_job(job_id, actor), index)
