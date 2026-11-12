"""
/jobs: what a person does to one of their own jobs wherever its card shows
-- hide it from its gallery (/generate, /clone, /clone-long,
/download-assets), or show it again (static/js/hidden-cards.js). Any tool's
job, only the session's own; a view choice only, the job and its files stay.
"""

from fastapi import APIRouter

from ofmhelpers.features.jobs import own
from ofmhelpers.schemas import JobVisibility
from ofmhelpers.schemas.access import Area
from ofmhelpers.web.dependencies import Access, Actor

router = APIRouter(prefix="/jobs", tags=["jobs"], dependencies=[Access(Area.JOBS)])


@router.post("/{job_id}/hidden")
def set_hidden(job_id: str, body: JobVisibility, actor: Actor) -> JobVisibility:
    return own.set_hidden(job_id, actor, body.hidden)
