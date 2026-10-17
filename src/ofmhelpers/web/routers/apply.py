"""
ofmhelpers/web/routers/apply.py

The one public write in the app: the application form on the `/` landing page
(templates/reachmodel.html) posts here. No login -- `/apply` is on
`settings.web.public_paths` -- but `WriteRateLimitMiddleware` still caps it
per IP, which is the only brake on a form the whole internet can reach.

The form's shape is `schemas.ApplicationForm` (the store takes it too).
Post/Redirect/Get back to `/?applied=1`, so a refresh can't submit twice.
"""

from typing import Annotated

from fastapi import APIRouter, Form
from fastapi.responses import RedirectResponse

from ofmhelpers.log import get_logger
from ofmhelpers.web.schemas import ApplicationForm
from ofmhelpers.web.stores import applications as applications_store

logger = get_logger(__name__)

router = APIRouter(tags=["apply"])


@router.post("/apply")
def submit_application(form: Annotated[ApplicationForm, Form()]):
    application = applications_store.add_application(form)
    logger.info("application received: %s", application.id)
    return RedirectResponse(url="/?applied=1", status_code=303)
