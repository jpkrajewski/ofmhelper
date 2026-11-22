from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from ofmhelpers.db.jobs.schema import Job
from ofmhelpers.features.jobs import audit
from ofmhelpers.web.access import Area
from ofmhelpers.web.dependencies import Access, Actor
from ofmhelpers.web.routers.registry import TOOLS
from ofmhelpers.web.templates_config import render

router = APIRouter(
    prefix="/action-log",
    tags=["action-log"],
    dependencies=[Access(Area.ACTION_LOG)],
)


def _status_url(job: Job) -> str | None:
    tool = TOOLS.get(job.task)
    return f"{tool.prefix}/jobs/{job.id}" if tool else None


@router.get("", response_class=HTMLResponse)
def dashboard(request: Request, actor: Actor) -> Response:
    view = audit.view(actor, _status_url)
    return render(request, "action_log.html", dict(view))
