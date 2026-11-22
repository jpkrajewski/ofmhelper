from pathlib import Path

from pydantic import BaseModel


class MoveStarted(BaseModel):
    job_id: str


class PostingWindow(BaseModel):
    start: float
    end: float
    open: bool


class Source(BaseModel):
    task: str
    files_prefix: str
    page: str


class DriveSpace(BaseModel):
    free_text: str = ""
    free_mb: int | None = None
    low: bool = False
    reconnect: bool = False
    folder_url: str
    buy_url: str


class _Creators(BaseModel, frozen=True):
    by_handle: dict[str, str]

    def of(self, handle: str) -> str | None:
        return self.by_handle.get(handle)


class _UploadFile(BaseModel, frozen=True):
    index: int
    path: Path


class _AccountFolder(BaseModel, frozen=True):
    folder_id: str
    kept: bool


class _RootListing(BaseModel, frozen=True):
    names: dict[str, str]
