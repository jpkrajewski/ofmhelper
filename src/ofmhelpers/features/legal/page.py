"""What /privacy and /terms show beside their text."""

from pydantic import BaseModel

from ofmhelpers.config import settings
from ofmhelpers.integrations.gdrive.client import SCOPES

# When the texts last changed: shown at the top of both pages.
UPDATED = "8 November 2026"
TELEGRAM = "https://t.me/ascendonboard"


class PageView(BaseModel):
    """Each field is a name `privacy.html` / `terms.html` reads."""

    updated: str
    contact_email: str
    telegram: str
    # The Google Drive access the app asks for, named on /privacy.
    drive_scopes: tuple[str, ...]


def page_view() -> PageView:
    return PageView(
        updated=UPDATED,
        contact_email=settings.web.contact_email,
        telegram=TELEGRAM,
        drive_scopes=SCOPES,
    )
