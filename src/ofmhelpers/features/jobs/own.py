"""A person's own jobs, as the routes every tool shares ask for them: one
job by id (`find`, `own`), and its card hidden or shown again."""

from ofmhelpers.db.stores.jobs import get_job, hide_job
from ofmhelpers.features.refusals import NotFoundError
from ofmhelpers.schemas import Job, JobVisibility

NO_JOB = "Job not found"


def find(job_id: str, actor: str) -> Job | None:
    """`actor`'s job, or None: gone, or someone else's."""
    return get_job(job_id, actor)


def own(job_id: str, actor: str) -> Job:
    job = get_job(job_id, actor)
    if job is None:
        raise NotFoundError(NO_JOB)
    return job


def set_hidden(job_id: str, actor: str, hidden: bool) -> JobVisibility:
    """A view choice only: the job and its files stay."""
    job = hide_job(job_id, actor, hidden)
    if job is None:
        raise NotFoundError(NO_JOB)
    return JobVisibility(hidden=job.hidden_at is not None)
