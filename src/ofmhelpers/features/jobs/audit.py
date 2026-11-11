"""/action-log: who ran what, when, and how it ended -- every user's jobs
and sign-ins. Another person's row is metadata only: no params (a prompt,
URLs, file names are their work) and no link (their job pages 404 for anyone
else). The viewer's own rows keep both."""

from collections.abc import Callable
from datetime import UTC, datetime

from pydantic import BaseModel

from ofmhelpers.db.stores.jobs import history
from ofmhelpers.db.stores.users import list_users
from ofmhelpers.schemas import ActionLogEntry, Job


class AuditView(BaseModel):
    """Each field is a name `action_log.html` reads."""

    jobs: list[ActionLogEntry]
    any_running: bool


class _Person(BaseModel):
    email: str | None
    role: str = "user"


def _entry(
    job: Job, who: dict[str, _Person], me: str, status_url: Callable[[Job], str | None]
) -> ActionLogEntry:
    """`who`: actor (a user id) -> the person; a removed user's id stays as
    it is. `me`: the viewer's actor -- only their own rows carry params and a
    link."""
    person = who.get(job.actor or "", _Person(email=job.actor))
    mine = job.actor == me
    return ActionLogEntry(
        task=job.task,
        actor=person.email,
        actor_role=person.role,
        status=job.status,
        params=job.params if mine else {},
        status_url=status_url(job) if mine else None,
        created_at_display=datetime.fromtimestamp(job.created_at, UTC).strftime(
            "%H:%M:%S"
        ),
    )


def view(viewer: str, status_url: Callable[[Job], str | None]) -> AuditView:
    """The history as stored: an audit trail is read, never healed -- a job
    whose files are gone stays listed (its link may 404). `status_url`: a
    job's status page, which the routers know."""
    who = {str(u.id): _Person(email=u.email, role=str(u.role)) for u in list_users()}
    jobs = [_entry(job, who, viewer, status_url) for job in history()]
    return AuditView(
        jobs=jobs, any_running=any(j.status in ("running", "queued") for j in jobs)
    )
