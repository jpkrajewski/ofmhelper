"""
/jobs: what a person does to one of their own jobs wherever its card shows
-- hide it from its gallery (/generate, /clone, /clone-long,
/download-assets), or show it again (static/js/hidden-cards.js). Any tool's
job, only the session's own; a view choice only, the job and its files stay.
"""

from fastapi import APIRouter, HTTPException

from ofmhelpers.db.stores.jobs import hide_job
from ofmhelpers.schemas import JobVisibility
from ofmhelpers.web.dependencies import Actor

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post("/{job_id}/hidden")
def set_hidden(job_id: str, body: JobVisibility, actor: Actor) -> JobVisibility:
    job = hide_job(job_id, actor, body.hidden)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return JobVisibility(hidden=job.hidden_at is not None)
