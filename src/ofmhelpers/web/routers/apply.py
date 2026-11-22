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
    if AuthMiddleware.session_user(request) is not None:
        return see_other(AuthMiddleware.HOME)
    return render(request, "reachmodel.html", {"form_token": form_token()})


@router.post("/apply")
def submit_application(
    request: Request, posted: Annotated[Submitted, Form()]
) -> Response:
    submit(posted, client_id(request))
    return see_other(_THANKS)
