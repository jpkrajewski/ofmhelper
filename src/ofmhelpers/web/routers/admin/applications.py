"""
ofmhelpers/web/routers/admin/applications.py

The CRM view of the public application form: every submission from the `/`
landing page, newest first, with everything the applicant typed. Read and
delete only -- the rows are written by routers/apply.py and never edited.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from ofmhelpers.web.middleware import AuthMiddleware
from ofmhelpers.web.stores import applications as applications_store
from ofmhelpers.web.templates_config import render

router = APIRouter(
    prefix="/applications",
    tags=["applications"],
    dependencies=[Depends(AuthMiddleware.require_admin)],
)


@router.get("")
def list_page(request: Request):
    return render(
        request,
        "applications.html",
        applications=applications_store.list_applications(),
    )


@router.post("/{application_id}/delete")
def delete_application(application_id: str):
    if not applications_store.delete_application(application_id):
        raise HTTPException(status_code=404, detail="Application not found")
    return RedirectResponse(url="/applications", status_code=303)
