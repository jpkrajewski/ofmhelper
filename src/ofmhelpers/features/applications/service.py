# Copyright (C) 2026 jpkrajewski

import logging
import time

from itsdangerous import BadSignature, URLSafeTimedSerializer

from ofmhelpers.config import settings
from ofmhelpers.db.applications import store
from ofmhelpers.db.applications.schema import Application, ApplicationForm
from ofmhelpers.features.refusals import NotFoundError

logger = logging.getLogger(__name__)


class Submitted(ApplicationForm):
    form_token: str = ""
    website: str = ""


def _tokens() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(
        settings.session.session_secret,
        salt="ofm-apply-form",
    )


def form_token() -> str:
    return _tokens().dumps(time.time())


def _token_problem(token: str) -> str | None:
    s = settings.web
    try:
        served = float(_tokens().loads(token, max_age=s.apply_token_ttl_s))
    except BadSignature, TypeError, ValueError:  # silent: the problem is the answer
        return "no valid form token"
    if time.time() - served < s.apply_min_fill_s:
        return "filled in too fast"
    return None


def submit(posted: Submitted, sender: str) -> None:
    form = ApplicationForm.model_validate(
        posted.model_dump(exclude={"form_token", "website"}),
    )
    reason = (
        "honeypot filled"
        if posted.website
        else _token_problem(posted.form_token) or form.spam_reason()
    )
    if reason is not None:
        logger.warning("application dropped as spam (%s) from %s", reason, sender)
        return
    application = store.add_application(form)
    logger.info("application received: %s", application.id)


def received() -> list[Application]:
    return store.list_applications()


def delete(application_id: str) -> None:
    if not store.delete_application(application_id):
        msg = "Application not found"
        raise NotFoundError(msg)
