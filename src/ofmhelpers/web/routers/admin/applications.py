"""
ofmhelpers/web/routers/admin/applications.py

The CRM view of the public application form: every submission from the `/`
landing page, newest first, with everything the applicant typed. Read and
delete only -- the rows are written by routers/apply.py and never edited.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from ofmhelpers.web.middleware import AuthMiddleware
from ofmhelpers.web.stores import applications as applications_store
from ofmhelpers.web.templates_config import get_templates

router = APIRouter(
    prefix="/applications",
    tags=["applications"],
    dependencies=[Depends(AuthMiddleware.require_admin)],
)


@router.get("")
def list_page(request: Request):
    rows = [
        {
            **a,
            "created_at_display": datetime.fromtimestamp(a["created_at"], UTC).strftime(
                "%Y-%m-%d %H:%M UTC"
            ),
        }
        for a in applications_store.list_applications()
    ]
    return get_templates().TemplateResponse(
        request, "applications.html", {"applications": rows}
    )


@router.post("/{application_id}/delete")
def delete_application(application_id: str):
    if not applications_store.delete_application(application_id):
        raise HTTPException(status_code=404, detail="Application not found")
    return RedirectResponse(url="/applications", status_code=303)
