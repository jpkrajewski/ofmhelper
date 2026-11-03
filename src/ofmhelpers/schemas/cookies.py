"""A user's own cookies.txt as /cookies shows it: never a value."""

from pydantic import BaseModel, ConfigDict

from ofmhelpers.integrations.downloaders.models import CookieJarSummary


class SavedCookies(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file_name: str
    uploaded_at: float
    summary: CookieJarSummary
