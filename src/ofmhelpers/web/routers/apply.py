"""
ofmhelpers/web/routers/apply.py

The public landing page (`/`, templates/reachmodel.html) and its application
form's POST (`/apply`): the one public write in the app. No login -- both are
on `settings.web.public_paths` -- but `WriteRateLimitMiddleware` caps
`/apply` per IP. Someone signed in never sees it: `/` sends them to /home.

A bot's submit is dropped and answered like a real one
(`features/applications/service.py`: the honeypot, the signed form token,
the shapes no person types). The form's shape is `schemas.ApplicationForm`
(the store takes it too). Post/Redirect/Get back to `/?applied=1`, so a
refresh can't submit twice.
"""

from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import Response

from ofmhelpers.features.applications.service import Submitted, form_token, submit
from ofmhelpers.web.middleware import AuthMiddleware
from ofmhelpers.web.middleware.ratelimit import client_id
from ofmhelpers.web.responses import see_other
from ofmhelpers.web.templates_config import render

router = APIRouter(tags=["apply"])

_THANKS = "/?applied=1"


@router.get("/")
def landing(request: Request) -> Response:
    # Signed in: the app, not the public page.
    if AuthMiddleware.session_user(request) is not None:
        return see_other(AuthMiddleware.HOME)
    return render(request, "reachmodel.html", form_token=form_token())


@router.post("/apply")
def submit_application(
    request: Request, posted: Annotated[Submitted, Form()]
) -> Response:
    submit(posted, client_id(request))
    return see_other(_THANKS)
