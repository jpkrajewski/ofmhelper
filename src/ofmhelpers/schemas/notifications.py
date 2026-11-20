from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class Notification(BaseModel):
    id: str
    event_ids: list[str]
    kind: Literal["info", "warn"] = "info"
    title: str
    text: str
    url: str
    at: datetime


class Notifications(BaseModel):
    items: list[Notification]
