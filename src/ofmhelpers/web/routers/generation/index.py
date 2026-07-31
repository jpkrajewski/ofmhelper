"""
ofmhelpers/web/routers/generation/index.py

Unified Higgsfield-style page for Seedance 2.0 / Kling 3.0 / Nano Banana Pro
(plus the Fake AI Model testing tool): one prompt+settings form (a tool
picker switches which fieldset is active) posting straight to each tool's
existing /run endpoint, and a non-blocking gallery of the last 20
generations across all of them that a click reloads back into the form.
"""

import json
from typing import Annotated

from fastapi import APIRouter, Query, Request

from ofmhelpers.config import settings
from ofmhelpers.web.auth import get_kie_api_key
from ofmhelpers.web.routers.generation.seedance import SeedanceModel
from ofmhelpers.web.routers.task_helpers import asset_card
from ofmhelpers.web.stores.jobs import list_jobs
from ofmhelpers.web.templates_config import templates

router = APIRouter(prefix="/generate", tags=["generate"])

GALLERY_LIMIT = settings.web.gallery_limit

TASK_LABELS = {
    "seedance": "Seedance 2.0",
    "kling3": "Kling 3.0",
    "nanobanana": "Nano Banana Pro",
    "fake_ai": "Fake AI Model",
    "replicate": "Replicate (Reel Clone)",
}
FILES_PREFIX = {
    "seedance": "/seedance/files",
    "kling3": "/kling3/files",
    "nanobanana": "/nanobanana/files",
    "fake_ai": "/fake-ai/files",
    "replicate": "/replicate/files",
}


def _gallery_card(job: dict) -> dict:
    assets = []
    if job["status"] == "done":
        prefix = f"{FILES_PREFIX[job['task']]}/{job['id']}"
        assets = [
            asset_card(f["name"], idx, prefix, remote_url=f.get("remote_url"))
            for idx, f in enumerate(job.get("result") or [])
        ]
    return {
        "job_id": job["id"],
        "task": job["task"],
        "task_label": TASK_LABELS[job["task"]],
        "status": job["status"],
        "error": job.get("error"),
        "params_json": json.dumps(job["params"]),
        # "/fake-ai/files" -> "/fake-ai": the router prefix generation.js
        # polls (/jobs/{id}/status) to resume still-running cards after a
        # page load.
        "poll_prefix": FILES_PREFIX[job["task"]].rsplit("/files", 1)[0],
        "assets": assets,
    }


def _gallery_page(offset: int) -> tuple[list[dict], int | None]:
    """One page of gallery cards plus the offset of the next page, or None when
    this was the last one. The absence of a next offset is what ends the
    infinite scroll, so there is no separate "has_more" flag to keep in step."""
    jobs = [j for j in list_jobs() if j["task"] in TASK_LABELS]
    page = jobs[offset : offset + GALLERY_LIMIT]
    next_offset = offset + GALLERY_LIMIT
    return (
        [_gallery_card(j) for j in page],
        next_offset if next_offset < len(jobs) else None,
    )


@router.get("")
def form(request: Request):
    gallery, next_offset = _gallery_page(0)
    return templates.TemplateResponse(
        request,
        "generate_form.html",
        {
            "models": [m.value for m in SeedanceModel],
            "kie_api_key": get_kie_api_key(request),
            "gallery": gallery,
            "next_offset": next_offset,
        },
    )


@router.get("/gallery")
def gallery_page(request: Request, offset: Annotated[int, Query(ge=0)] = 0):
    """The next page of gallery cards as a fragment gallery-scroll.js appends --
    same partial the page's first page renders, so an appended card is
    indistinguishable from a server-rendered one."""
    gallery, next_offset = _gallery_page(offset)
    return templates.TemplateResponse(
        request,
        "generate_gallery_fragment.html",
        {"gallery": gallery, "next_offset": next_offset},
    )
