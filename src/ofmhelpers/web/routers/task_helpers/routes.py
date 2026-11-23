# Copyright (C) 2026 jpkrajewski

from typing import Annotated
lazy from collections.abc import Callable

from fastapi import APIRouter, Query, Request
from fastapi.responses import Response

from ofmhelpers.db.jobs.schema import Job
from ofmhelpers.features.jobs import own
from ofmhelpers.features.jobs.models import JobStatusPayload
from ofmhelpers.web.dependencies import Actors
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
    try:
        return pending_message(job)
    except KeyError:  # silent: a job of an older release: plain wording
        return "Working…"


def add_job_routes(
    router: APIRouter,
    title: str,
    pending_message: Callable[[Job], str],
    back_url: str,
    back_label: str,
    show_inputs: bool = False,
) -> None:
    files_prefix = f"{router.prefix}/files"

    @router.get("/jobs/{job_id}")
    def job_status(request: Request, job_id: str, actors: Actors) -> Response:
        job = own.own(job_id, actors)
        found = job_assets(job, files_prefix)
        return render(
            request,
            "job_status.html",
            {
                "job": job,
                "assets": found.assets,
                "failed_sources": found.failed_sources,
                "job_inputs": job_inputs(job) if show_inputs else None,
                "title": title,
                "pending_message": _pending_text(pending_message, job),
                "back_url": back_url,
                "back_label": back_label,
            },
        )

    @router.get("/jobs/{job_id}/status")
    def job_status_json(job_id: str, actors: Actors) -> JobStatusPayload:
        return job_status_payload(own.find(job_id, actors), files_prefix)

    @router.get("/files/{job_id}/{index}")
    def download_file(
        job_id: str,
        index: int,
        actors: Actors,
        dl: Annotated[int, Query()] = 0,
    ) -> Response:
        return serve_job_file(own.find(job_id, actors), index, as_attachment=bool(dl))

    @router.get("/files/{job_id}/{index}/preview")
    def preview_file(job_id: str, index: int, actors: Actors) -> Response:
        return serve_job_preview(own.find(job_id, actors), index, files_prefix)

    @router.get("/files/{job_id}/{index}/poster")
    def poster_file(job_id: str, index: int, actors: Actors) -> Response:
        return serve_job_poster(own.find(job_id, actors), index)
