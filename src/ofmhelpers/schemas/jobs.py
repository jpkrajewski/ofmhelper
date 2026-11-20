from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, computed_field

from ofmhelpers.schemas.errors import (
    ErrorText,
    download_reason,
    error_line,
    error_reason,
    error_text,
)
from ofmhelpers.schemas.persistence import JobPreview


class AssetCard(BaseModel):
    name: str
    index: int
    kind: str
    view_url: str
    download_url: str
    download_name: str
    local_fallback_url: str | None = None
    preview_url: str | None = None
    poster_url: str | None = None
    source: str | None = None
    removed: bool = False
    removed_reason: str | None = None
    removed_error: str | None = None


class FailedSource(BaseModel):
    source: str
    error: str

    @computed_field  # type: ignore[prop-decorator]
    @property
    def link(self) -> str:
        parts = urlsplit(self.source)
        if not parts.netloc:
            return self.source
        host = parts.netloc.removeprefix("www.")
        return f"{host}{parts.path.rstrip('/')}"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def reason(self) -> str:
        return download_reason(self.error)


class JobStatusPayload(BaseModel):
    job_id: str
    task: str
    params: dict[str, Any]
    status: str
    error: str | None
    result: list[AssetCard]
    failed_sources: list[FailedSource]
    preview: JobPreview | None = None
    stage: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def error_reason(self) -> str:
        return error_reason(self.error)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def error_line(self) -> str:
        return error_line(self.error)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def error_text(self) -> ErrorText:
        return error_text(self.error)


class JobQueued(BaseModel):
    job_id: str


class JobsQueued(BaseModel):
    job_ids: list[str]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def job_id(self) -> str:
        return self.job_ids[0]


class JobVisibility(BaseModel):
    hidden: bool


class CloneQueued(BaseModel):
    job_ids: list[str]
    html: str
    batch: str | None = None


class ReferenceAsset(BaseModel):
    name: str
    kind: str
    view_url: str


class InputSetting(BaseModel):
    label: str
    value: Any


class InputFileGroup(BaseModel):
    label: str
    assets: list[ReferenceAsset]


class JobInputs(BaseModel):
    settings: list[InputSetting]
    long_text: list[InputSetting]
    file_groups: list[InputFileGroup]


class ActionLogEntry(BaseModel):
    task: str
    actor: str | None
    actor_role: str = "user"
    status: str
    params: dict[str, Any]
    status_url: str | None
    created_at_display: str
