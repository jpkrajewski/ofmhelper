from fastapi import APIRouter

from ofmhelpers.auth.access import Action, Area
from ofmhelpers.features.jobs import own
from ofmhelpers.schemas import JobVisibility
from ofmhelpers.web.dependencies import Access, Actors, Can

router = APIRouter(prefix="/jobs", tags=["jobs"], dependencies=[Access(Area.JOBS)])


@router.post("/{job_id}/hidden", dependencies=[Can(Action.HIDE)])
def set_hidden(job_id: str, body: JobVisibility, actors: Actors) -> JobVisibility:
    return own.set_hidden(job_id, actors, body.hidden)
