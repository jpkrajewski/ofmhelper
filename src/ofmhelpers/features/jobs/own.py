from ofmhelpers.db.stores.jobs import Readers, get_job, hide_job
from ofmhelpers.features.refusals import NotFoundError
from ofmhelpers.schemas import Job, JobVisibility

NO_JOB = "Job not found"


def find(job_id: str, actors: Readers) -> Job | None:
    return get_job(job_id, actors)


def own(job_id: str, actors: Readers) -> Job:
    job = get_job(job_id, actors)
    if job is None:
        raise NotFoundError(NO_JOB)
    return job


def set_hidden(job_id: str, actors: Readers, hidden: bool) -> JobVisibility:
    job = hide_job(job_id, actors, hidden)
    if job is None:
        raise NotFoundError(NO_JOB)
    return JobVisibility(hidden=job.hidden_at is not None)
