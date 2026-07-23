from datetime import datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from ofmhelpers.web.templates_config import templates
from ofmhelpers.web.jobs import list_jobs
from ofmhelpers.web.auth import require_admin

router = APIRouter(
    prefix="/action-log", tags=["action-log"], dependencies=[Depends(require_admin)]
)

# Maps a job's "task" field to the URL prefix of its dedicated status page.
# Add one line here whenever you add a new task type -- everything else
# on this page is generic.
TASK_STATUS_PREFIX = {
    "download_videos": "/download-videos",
    "download_images": "/download-images",
    "clean_images": "/clean-images",
    "seedance": "/seedance",
    "elevenlabs": "/helpers/elevenlabs",
    "radio_comms": "/helpers/radio-comms",
    "scraper": "/helpers/scraper",
    "nanobanana": "/nanobanana",
    "kling3": "/kling3",
    "fake_ai": "/fake-ai",
}


def _status_url(job: dict) -> str | None:
    prefix = TASK_STATUS_PREFIX.get(job["task"])
    return f"{prefix}/jobs/{job['id']}" if prefix else None


@router.get("", response_class=HTMLResponse)
def dashboard(request: Request):
    jobs = list_jobs()
    for job in jobs:
        job["status_url"] = _status_url(job)
        job["created_at_display"] = datetime.fromtimestamp(job["created_at"]).strftime(
            "%H:%M:%S"
        )

    any_running = any(j["status"] in ("running", "queued") for j in jobs)
    return templates.TemplateResponse(
        request, "action_log.html", {"jobs": jobs, "any_running": any_running}
    )
