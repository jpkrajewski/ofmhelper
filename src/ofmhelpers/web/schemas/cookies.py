"""A user's own cookies.txt as /cookies shows it: never a value."""

from pydantic import BaseModel, ConfigDict

from ofmhelpers.downloaders.models import CookieJarSummary


class SavedCookies(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file_name: str
    uploaded_at: float
    summary: CookieJarSummary
