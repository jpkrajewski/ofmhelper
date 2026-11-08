"""/privacy and /terms: the legal pages of the public site, linked from the
landing page's footer and from the Google OAuth consent screen (Branding).
Public (`settings.web.public_paths`): Google's reviewers fetch them signed
out, and a login wall there stalls the app's verification."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from ofmhelpers.config import settings
from ofmhelpers.integrations.gdrive.client import SCOPES as DRIVE_SCOPES
from ofmhelpers.web.templates_config import render

router = APIRouter(tags=["legal"])

# When the texts last changed: shown at the top of both pages.
UPDATED = "8 November 2026"
TELEGRAM = "https://t.me/ascendonboard"


def _page(request: Request, name: str) -> Response:
    return render(
        request,
        name,
        updated=UPDATED,
        contact_email=settings.web.contact_email,
        telegram=TELEGRAM,
        drive_scopes=DRIVE_SCOPES,
    )


@router.get("/privacy", response_class=HTMLResponse)
def privacy(request: Request) -> Response:
    return _page(request, "privacy.html")


@router.get("/terms", response_class=HTMLResponse)
def terms(request: Request) -> Response:
    return _page(request, "terms.html")
