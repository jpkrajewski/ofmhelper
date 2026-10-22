"""
ofmhelpers/web/routers/apply.py

The public landing page (`/`, templates/reachmodel.html) and its application
form's POST (`/apply`): the one public write in the app. No login -- both are
on `settings.web.public_paths` -- but `WriteRateLimitMiddleware` caps
`/apply` per IP.

Spam bots filled it (prod, 2026-10-22: every lead but one). A submit is a
bot's, and is dropped -- answered like a real one, so the bot learns
nothing, and logged -- when:

- the hidden `website` field (a honeypot humans never see) is filled;
- its `form_token` (a signed time, put in the page when served) is missing,
  forged, older than `apply_token_ttl_s`, or younger than
  `apply_min_fill_s` (no person fills the form in seconds);
- `ApplicationForm.spam_reason` says so (links, handle/phone shapes no
  person types; the browser checks the same shapes, so people are told).

The form's shape is `schemas.ApplicationForm` (the store takes it too).
Post/Redirect/Get back to `/?applied=1`, so a refresh can't submit twice.
"""

import time
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse
from itsdangerous import BadSignature, URLSafeTimedSerializer

from ofmhelpers.config import settings
from ofmhelpers.log import get_logger
from ofmhelpers.web.middleware.ratelimit import client_id
from ofmhelpers.web.schemas import ApplicationForm
from ofmhelpers.web.stores import applications as applications_store
from ofmhelpers.web.templates_config import render

logger = get_logger(__name__)

router = APIRouter(tags=["apply"])

_THANKS = "/?applied=1"


def _tokens() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(
        settings.session.session_secret, salt="ofm-apply-form"
    )


def form_token() -> str:
    """The time the form was served, signed: what `/apply` checks."""
    return _tokens().dumps(time.time())


def _token_problem(token: str) -> str | None:
    s = settings.web
    try:
        served = float(_tokens().loads(token, max_age=s.apply_token_ttl_s))
    except (BadSignature, TypeError, ValueError):
        return "no valid form token"
    if time.time() - served < s.apply_min_fill_s:
        return "filled in too fast"
    return None


@router.get("/")
def landing(request: Request):
    return render(request, "reachmodel.html", form_token=form_token())


class _Submitted(ApplicationForm):
    """What the page posts: the application plus the two bot traps."""

    form_token: str = ""
    website: str = ""


@router.post("/apply")
def submit_application(request: Request, posted: Annotated[_Submitted, Form()]):
    form = ApplicationForm.model_validate(
        posted.model_dump(exclude={"form_token", "website"})
    )
    reason = (
        "honeypot filled"
        if posted.website
        else _token_problem(posted.form_token) or form.spam_reason()
    )
    if reason is not None:
        logger.warning(
            "application dropped as spam (%s) from %s", reason, client_id(request)
        )
        return RedirectResponse(url=_THANKS, status_code=303)
    application = applications_store.add_application(form)
    logger.info("application received: %s", application.id)
    return RedirectResponse(url=_THANKS, status_code=303)
