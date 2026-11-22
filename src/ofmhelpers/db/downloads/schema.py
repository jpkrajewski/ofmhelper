from datetime import date, datetime

from pydantic import BaseModel, ConfigDict


class Download(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: int
    user_id: int
    job_id: str
    url: str
    position: int
    file_path: str
    asset_path: str | None = None
    stored: bool = True
    created_at: datetime
    last_used_at: datetime
    removed_at: datetime | None = None


class DownloadedFile(BaseModel):
    model_config = ConfigDict(frozen=True)

    url: str
    position: int
    file_path: str
    asset_path: str | None = None


class RemovedFile(BaseModel):
    model_config = ConfigDict(frozen=True)

    job_id: str
    position: int


class SheetQueued(BaseModel):
    job_id: str | None = None
    day: date
    new: int = 0
    already: int = 0
    skipped: int = 0
    cooldown_s: int = 0


class DownloadsQueued(BaseModel):
    job_id: str | None = None
    job_ids: list[str] = []
    again: list[str] = []
