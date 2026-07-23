"""
ofmhelpers/web/routers/generate.py

Unified Higgsfield-style page for Seedance 2.0 / Kling 3.0 / Nano Banana Pro
(plus the Fake AI Model testing tool): one prompt+settings form (a tool
picker switches which fieldset is active) posting straight to each tool's
existing /run endpoint, and a non-blocking gallery of the last 20
generations across all of them that a click reloads back into the form.
"""

import json

from fastapi import APIRouter, Request

from ofmhelpers.web.templates_config import templates
from ofmhelpers.web.jobs import list_jobs
from ofmhelpers.web.auth import get_kie_api_key
from ofmhelpers.web.routers.seedance import SeedanceModel
from ofmhelpers.web.routers.task_helpers import asset_card

router = APIRouter(prefix="/generate", tags=["generate"])

GALLERY_LIMIT = 20

TASK_LABELS = {
    "seedance": "Seedance 2.0",
    "kling3": "Kling 3.0",
    "nanobanana": "Nano Banana Pro",
    "fake_ai": "Fake AI Model",
}
FILES_PREFIX = {
    "seedance": "/seedance/files",
    "kling3": "/kling3/files",
    "nanobanana": "/nanobanana/files",
    "fake_ai": "/fake-ai/files",
}


def _gallery_card(job: dict) -> dict:
    assets = []
    if job["status"] == "done":
        prefix = f"{FILES_PREFIX[job['task']]}/{job['id']}"
        assets = [
            asset_card(f["name"], idx, prefix)
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


@router.get("")
def form(request: Request):
    recent = [j for j in list_jobs() if j["task"] in TASK_LABELS][:GALLERY_LIMIT]
    return templates.TemplateResponse(
        request,
        "generate_form.html",
        {
            "models": [m.value for m in SeedanceModel],
            "kie_api_key": get_kie_api_key(request),
            "gallery": [_gallery_card(j) for j in recent],
        },
    )
