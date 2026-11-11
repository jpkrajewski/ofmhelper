"""The jobs a person may see, as the routes every tool shares ask for them:
one job by id (`find`, `own`: read by its maker and by the maker's owner, so
they take everyone the reader sees, `Viewer.actors`), and its card hidden or
shown again (the maker's own choice: one actor)."""

from ofmhelpers.db.stores.jobs import Readers, get_job, hide_job
from ofmhelpers.features.refusals import NotFoundError
from ofmhelpers.schemas import Job, JobVisibility

NO_JOB = "Job not found"


def find(job_id: str, actors: Readers) -> Job | None:
    """The job if one of `actors` made it, or None: gone, or someone
    else's."""
    return get_job(job_id, actors)


def own(job_id: str, actors: Readers) -> Job:
    job = get_job(job_id, actors)
    if job is None:
        raise NotFoundError(NO_JOB)
    return job


def set_hidden(job_id: str, actor: str, hidden: bool) -> JobVisibility:
    """A view choice only: the job and its files stay."""
    job = hide_job(job_id, actor, hidden)
    if job is None:
        raise NotFoundError(NO_JOB)
    return JobVisibility(hidden=job.hidden_at is not None)
