from pydantic import BaseModel


class DriveConnection(BaseModel):
    """A connected Google Drive: whose, and the token uploads use."""

    id: str
    email: str
    refresh_token: str
