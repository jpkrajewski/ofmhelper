"""
/action-log (admins): an audit trail of who ran what, when, and how it
ended -- every user's jobs and sign-ins. Another person's row is metadata
only: no summary (a prompt, URLs, file names are their work) and no link
(their job pages 404 for anyone else). The admin's own rows keep both
(`features/jobs/audit.py`).
"""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from ofmhelpers.features.jobs import audit
from ofmhelpers.schemas import Job
from ofmhelpers.web.dependencies import Actor, AdminOnly
from ofmhelpers.web.routers.registry import TOOLS
from ofmhelpers.web.templates_config import render

router = APIRouter(
    prefix="/action-log",
    tags=["action-log"],
    dependencies=[AdminOnly],
)


def _status_url(job: Job) -> str | None:
    """The job's status page, or None for a task with no tool: login/logout
    events, and old jobs of a tool since removed."""
    tool = TOOLS.get(job.task)
    return f"{tool.prefix}/jobs/{job.id}" if tool else None


@router.get("", response_class=HTMLResponse)
def dashboard(request: Request, actor: Actor) -> Response:
    view = audit.view(actor, _status_url)
    return render(request, "action_log.html", **dict(view))
