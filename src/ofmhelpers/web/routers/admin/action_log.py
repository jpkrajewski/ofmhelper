"""
/action-log (admins): an audit trail of who ran what, when, and how it
ended -- every user's jobs and sign-ins. Another person's row is metadata
only: no summary (a prompt, URLs, file names are their work) and no link
(their job pages 404 for anyone else). The admin's own rows keep both.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from ofmhelpers.db.stores.jobs import history
from ofmhelpers.db.stores.users import list_users
from ofmhelpers.schemas import ActionLogEntry, Job
from ofmhelpers.web.middleware import AuthMiddleware
from ofmhelpers.web.routers.registry import TOOLS
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


def _entry(job: Job, who: dict[str, tuple[str, str]], me: str) -> ActionLogEntry:
    """`who`: actor (a user id) -> (email, role); a removed user's id stays
    as it is. `me`: the viewer's actor -- only their own rows carry params
    and a link."""
    email, role = who.get(job.actor or "", (job.actor, "user"))
    mine = job.actor == me
    return ActionLogEntry(
        task=job.task,
        actor=email,
        actor_role=role,
        status=job.status,
        params=job.params if mine else {},
        status_url=_status_url(job) if mine else None,
        created_at_display=datetime.fromtimestamp(job.created_at, UTC).strftime(
            "%H:%M:%S"
        ),
    )


@router.get("", response_class=HTMLResponse)
def dashboard(request: Request):
    """The history as stored: an audit trail is read, never healed -- a job
    whose files are gone stays listed (its link may 404)."""
    who = {str(u.id): (u.email, str(u.role)) for u in list_users()}
    me = AuthMiddleware.actor(request)
    jobs = [_entry(job, who, me) for job in history()]
    any_running = any(j.status in ("running", "queued") for j in jobs)
    return render(request, "action_log.html", jobs=jobs, any_running=any_running)
