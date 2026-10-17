from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from ofmhelpers.web.middleware import AuthMiddleware
from ofmhelpers.web.routers.registry import TOOLS
from ofmhelpers.web.schemas import ActionLogEntry, Job
from ofmhelpers.web.stores.jobs import list_jobs
from ofmhelpers.web.templates_config import render

router = APIRouter(
    prefix="/action-log",
    tags=["action-log"],
    dependencies=[Depends(AuthMiddleware.require_admin)],
)


def _status_url(job: Job) -> str | None:
    """The job's status page, or None for a task with no tool: login/logout
    events, and old jobs of a tool since removed."""
    tool = TOOLS.get(job.task)
    return f"{tool.prefix}/jobs/{job.id}" if tool else None


def _entry(job: Job) -> ActionLogEntry:
    return ActionLogEntry(
        task=job.task,
        actor=job.actor,
        status=job.status,
        params=job.params,
        status_url=_status_url(job),
        created_at_display=datetime.fromtimestamp(job.created_at, UTC).strftime(
            "%H:%M:%S"
        ),
    )


@router.get("", response_class=HTMLResponse)
def dashboard(request: Request):
    jobs = [_entry(job) for job in list_jobs()]
    any_running = any(j.status in ("running", "queued") for j in jobs)
    return render(request, "action_log.html", jobs=jobs, any_running=any_running)
