"""
ofmhelpers/web/routers/admin/applications.py

The CRM view of the public application form: every submission from the `/`
landing page, newest first, with everything the applicant typed. Read and
delete only -- the rows are written by routers/apply.py and never edited.
"""

from fastapi import APIRouter, Request
from fastapi.responses import Response

from ofmhelpers.features.applications import service
from ofmhelpers.schemas.access import Area
from ofmhelpers.web.dependencies import Access
from ofmhelpers.web.responses import see_other
from ofmhelpers.web.templates_config import render

router = APIRouter(
    prefix="/applications",
    tags=["applications"],
    dependencies=[Access(Area.APPLICATIONS)],
)


@router.get("")
def list_page(request: Request) -> Response:
    return render(request, "applications.html", applications=service.received())


@router.post("/{application_id}/delete")
def delete_application(application_id: str) -> Response:
    service.delete(application_id)
    return see_other("/applications")
