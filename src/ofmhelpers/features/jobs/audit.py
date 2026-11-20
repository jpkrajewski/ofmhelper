from collections.abc import Callable
from datetime import UTC, datetime

from pydantic import BaseModel

from ofmhelpers.db.stores.jobs import history
from ofmhelpers.db.stores.users import list_users
from ofmhelpers.schemas import ActionLogEntry, Job


class AuditView(BaseModel):
    jobs: list[ActionLogEntry]
    any_running: bool


class _Person(BaseModel):
    email: str | None
    role: str = "user"


def _entry(
    job: Job, who: dict[str, _Person], me: str, status_url: Callable[[Job], str | None]
) -> ActionLogEntry:
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
    who = {str(u.id): _Person(email=u.email, role=str(u.role)) for u in list_users()}
    jobs = [_entry(job, who, viewer, status_url) for job in history()]
    return AuditView(
        jobs=jobs, any_running=any(j.status in ("running", "queued") for j in jobs)
    )
