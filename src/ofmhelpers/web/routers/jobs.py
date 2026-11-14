"""
/jobs: what a person does to one of their own jobs wherever its card shows
-- hide it from its gallery (/generate, /clone, /clone-long,
/download-assets), or show it again (static/js/hidden-cards.js). Any tool's
job, only the session's own; a view choice only, the job and its files stay.
"""

from fastapi import APIRouter

from ofmhelpers.auth.access import Action, Area
from ofmhelpers.features.jobs import own
from ofmhelpers.schemas import JobVisibility
from ofmhelpers.web.dependencies import Access, Actors, Can

router = APIRouter(prefix="/jobs", tags=["jobs"], dependencies=[Access(Area.JOBS)])


# Their own job or one of their VAs' (`Actors`); a VA hides nothing
# (`Action.HIDE`: the owner must see everything a VA made).
@router.post("/{job_id}/hidden", dependencies=[Can(Action.HIDE)])
def set_hidden(job_id: str, body: JobVisibility, actors: Actors) -> JobVisibility:
    return own.set_hidden(job_id, actors, body.hidden)
