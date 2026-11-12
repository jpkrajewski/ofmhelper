"""What one source URL's download comes back as: the downloaders
(`integrations/downloaders`) return it and a grouped download job stores a
list of them."""

from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict


class DownloadResult(BaseModel):
    """One source URL: whether it downloaded, and the files it produced (a
    post can hold several), or why it failed. A grouped download job stores a
    list of these, dumped with `mode="json"` so the paths become strings."""

    model_config = ConfigDict(extra="forbid")

    url: str
    success: bool
    output_paths: list[Path] = []
    error: str | None = None


class Download(BaseModel):
    """One downloaded file (`downloads`): the link it came from, its place
    among its job's files (`position`, the `{i}` of `/files/{job}/{i}`),
    where it is (`file_path`, and `asset_path`: its copy in the asset
    store) and whether the video is still on this server (`stored`; the
    retention sweep removes it and keeps the row and the poster)."""

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
    """What a download job writes of one of its files when it finishes."""

    model_config = ConfigDict(frozen=True)

    url: str
    position: int
    file_path: str
    asset_path: str | None = None


class RemovedFile(BaseModel):
    """A job's file the sweep removed on purpose: its card stays."""

    model_config = ConfigDict(frozen=True)

    job_id: str
    position: int


class DownloadsQueued(BaseModel):
    """`POST /download-media/run`: `job_id` / `job_ids` = the new job the
    page polls, when a pasted link was new (none: every link was downloaded
    before). `again`: the jobs that already hold a pasted link, the last
    pasted first -- their cards go to the first place; one whose video had
    been removed is running again."""

    job_id: str | None = None
    job_ids: list[str] = []
    again: list[str] = []
