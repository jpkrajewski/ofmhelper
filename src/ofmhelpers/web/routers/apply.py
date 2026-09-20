"""
ofmhelpers/web/routers/apply.py

The one public write in the app: the application form on the `/` landing page
(templates/reachmodel.html) posts here. No login -- `/apply` is on
`settings.web.public_paths` -- but `WriteRateLimitMiddleware` still caps it
per IP, which is the only brake on a form the whole internet can reach.

Field names here are contract with that one template, so the shape stays
declared in this router rather than in `schemas/` (see CLAUDE.md).
Post/Redirect/Get back to `/?applied=1`, so a refresh can't submit twice.
"""

from typing import Annotated

from fastapi import APIRouter, Form
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, field_validator, model_validator

from ofmhelpers.log import get_logger
from ofmhelpers.web.stores import applications as applications_store

logger = get_logger(__name__)

router = APIRouter(tags=["apply"])


class ApplicationForm(BaseModel):
    """What the landing page collects. The two `<select>`s and the Instagram
    handle are optional: their placeholder option posts an empty string, and a
    lead who skipped a dropdown is still a lead.

    `phone` and `telegram_handle` are individually optional but not jointly:
    the form asks for one direct channel, and the browser cannot express
    "either of these two", so the rule is enforced here."""

    first_name: str
    last_name: str
    email: str
    phone: str = ""
    telegram_handle: str = ""
    instagram_handle: str = ""
    monthly_revenue: str = ""
    experience_level: str = ""
    goals: str = ""

    @field_validator("*", mode="after")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()

    @field_validator("first_name", "last_name", mode="after")
    @classmethod
    def _required(cls, v: str) -> str:
        if not v:
            msg = "must not be blank"
            raise ValueError(msg)
        return v

    @field_validator("email", mode="after")
    @classmethod
    def _looks_like_an_address(cls, v: str) -> str:
        # Deliberately not a full RFC check (that needs email-validator, a dep
        # this app doesn't carry): the point is to reject an empty or obviously
        # junk box, not to prove the address is deliverable.
        local, _, domain = v.partition("@")
        if not local or "." not in domain:
            msg = "must be an email address"
            raise ValueError(msg)
        return v

    @model_validator(mode="after")
    def _one_direct_channel(self) -> "ApplicationForm":
        if not self.phone and not self.telegram_handle:
            msg = "give a phone number or a Telegram handle"
            raise ValueError(msg)
        return self


@router.post("/apply")
def submit_application(form: Annotated[ApplicationForm, Form()]):
    application = applications_store.add_application(form.model_dump(mode="json"))
    logger.info("application received: %s", application["id"])
    return RedirectResponse(url="/?applied=1", status_code=303)
