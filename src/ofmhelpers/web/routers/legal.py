"""/privacy and /terms: the public pages Google's OAuth consent screen links
to (Drive uploads, see routers/drive.py), also linked from the landing page
footer. Public via `settings.web.public_paths`."""

from fastapi import APIRouter, Request

from ofmhelpers.config import settings
from ofmhelpers.web.templates_config import render

router = APIRouter(tags=["legal"])

# Bump with any change to the policy text.
UPDATED = "18 October 2026"


@router.get("/privacy")
def privacy(request: Request):
    return render(
        request,
        "privacy.html",
        contact_email=settings.web.contact_email,
        updated=UPDATED,
    )


@router.get("/terms")
def terms(request: Request):
    return render(
        request,
        "terms.html",
        contact_email=settings.web.contact_email,
        updated=UPDATED,
    )
