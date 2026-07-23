"""
ofmhelpers/web/routers/download_assets.py

Unified page for Download videos / Download images / Clean images, in the
same style as /generate: one form with a tool picker on the left, and a
non-blocking gallery of the last 20 runs across all three tools on the
right. Submissions POST to each tool's existing /run endpoint.
"""

import json

from fastapi import APIRouter, Request

from ofmhelpers.web.templates_config import templates
from ofmhelpers.web.jobs import list_jobs
from ofmhelpers.web.routers.task_helpers import asset_card, flatten_grouped_results

router = APIRouter(prefix="/download-assets", tags=["download-assets"])

GALLERY_LIMIT = 20

TASK_LABELS = {
    "download_videos": "Download videos",
    "download_images": "Download images",
    "clean_images": "Clean images",
}
FILES_PREFIX = {
    "download_videos": "/download-videos/files",
    "download_images": "/download-images/files",
    "clean_images": "/clean-images/files",
}
GROUPED_TASKS = {"download_videos", "download_images"}


def _gallery_card(job: dict) -> dict:
    assets = []
    failed_sources = []
    if job["status"] == "done":
        if job["task"] in GROUPED_TASKS:
            assets, failed_sources = flatten_grouped_results(
                job, FILES_PREFIX[job["task"]]
            )
        else:
            assets = [
                asset_card(f["name"], idx, f"{FILES_PREFIX[job['task']]}/{job['id']}")
                for idx, f in enumerate(job.get("result") or [])
            ]
    return {
        "job_id": job["id"],
        "task": job["task"],
        "task_label": TASK_LABELS[job["task"]],
        "status": job["status"],
        "error": job.get("error"),
        "failed_sources": failed_sources,
        "params_json": json.dumps(job["params"]),
        # "/download-videos/files" -> "/download-videos": the router prefix
        # generation.js polls to resume still-running cards after page load.
        "poll_prefix": FILES_PREFIX[job["task"]].rsplit("/files", 1)[0],
        "assets": assets,
    }


@router.get("")
def form(request: Request):
    recent = [j for j in list_jobs() if j["task"] in TASK_LABELS][:GALLERY_LIMIT]
    return templates.TemplateResponse(
        request,
        "download_assets_form.html",
        {"gallery": [_gallery_card(j) for j in recent]},
    )
