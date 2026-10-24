"""
/jobs: what a person does to one of their own jobs wherever its card shows
-- hide it from its gallery (/generate, /clone, /clone-long,
/download-assets), or show it again (static/js/hidden-cards.js). Any tool's
job, only the session's own; a view choice only, the job and its files stay.
"""

from fastapi import APIRouter, HTTPException, Request

from ofmhelpers.web.middleware import AuthMiddleware
from ofmhelpers.web.schemas import JobVisibility
from ofmhelpers.web.stores.jobs import hide_job

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post("/{job_id}/hidden")
def set_hidden(request: Request, job_id: str, body: JobVisibility) -> JobVisibility:
    job = hide_job(job_id, AuthMiddleware.actor(request), body.hidden)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return JobVisibility(hidden=job.hidden_at is not None)
