from pydantic import BaseModel

from ofmhelpers.config import settings
from ofmhelpers.integrations.gdrive.client import SCOPES

UPDATED = "8 November 2026"
TELEGRAM = "https://t.me/ascendonboard"


class PageView(BaseModel):
    updated: str
    contact_email: str
    telegram: str
    drive_scopes: tuple[str, ...]


def page_view() -> PageView:
    return PageView(
        updated=UPDATED,
        contact_email=settings.web.contact_email,
        telegram=TELEGRAM,
        drive_scopes=SCOPES,
    )
