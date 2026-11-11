"""The landing page's application form: what `/apply` does with a submit, and
the admin's list of what came in (/applications).

Spam bots filled it (prod, 2026-10-22: every lead but one). A submit is a
bot's, and is dropped -- answered like a real one, so the bot learns nothing,
and logged -- when:

- the hidden `website` field (a honeypot humans never see) is filled;
- its `form_token` (a signed time, put in the page when served) is missing,
  forged, older than `apply_token_ttl_s`, or younger than `apply_min_fill_s`
  (no person fills the form in seconds);
- `ApplicationForm.spam_reason` says so (links, handle/phone shapes no person
  types; the browser checks the same shapes, so people are told)."""

import time

from itsdangerous import BadSignature, URLSafeTimedSerializer

from ofmhelpers.config import settings
from ofmhelpers.db.stores import applications as store
from ofmhelpers.features.refusals import NotFoundError
from ofmhelpers.log import get_logger
from ofmhelpers.schemas import Application, ApplicationForm

logger = get_logger(__name__)


class Submitted(ApplicationForm):
    """What the page posts: the application plus the two bot traps."""

    form_token: str = ""
    website: str = ""


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
    except BadSignature, TypeError, ValueError:
        return "no valid form token"
    if time.time() - served < s.apply_min_fill_s:
        return "filled in too fast"
    return None


def submit(posted: Submitted, sender: str) -> None:
    """Stored, unless it is a bot's: then dropped and logged with `sender`."""
    form = ApplicationForm.model_validate(
        posted.model_dump(exclude={"form_token", "website"})
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
    """Newest first."""
    return store.list_applications()


def delete(application_id: str) -> None:
    if not store.delete_application(application_id):
        msg = "Application not found"
        raise NotFoundError(msg)
